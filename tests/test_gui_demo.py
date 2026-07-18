"""Hosted-demo hardening tests: ``create_app(demo=True)`` + :mod:`latextify.gui.demo`.

The demo posture (Hugging Face Space) must disable the server-filesystem
endpoints, lower the upload cap, rate-limit conversions, inject the privacy
banner, and swap the loopback-only auth for same-origin + secret -- all without
changing the local tool's behavior (every ``demo=False`` path is covered by
tests/test_gui.py; the regression tests here pin the boundary between the two).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from latextify.gui.demo import RateLimiter, inject_demo_banner
from latextify.gui.guard import SECRET_HEADER
from latextify.gui.server import create_app

FIXTURES = Path(__file__).parent / "fixtures"

_TEST_SECRET = "test-gui-secret"
#: The kind of public hostname a Space serves from -- anything non-loopback.
_PUBLIC_HOST = "someuser-latextify.hf.space"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _demo_app(tmp_path: Path):
    return create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET, demo=True)


def _demo_client(tmp_path: Path, app=None, *, secret: bool = True) -> TestClient:
    application = app if app is not None else _demo_app(tmp_path)
    headers = {SECRET_HEADER: _TEST_SECRET} if secret else {}
    return TestClient(application, base_url=f"https://{_PUBLIC_HOST}", headers=headers)


def _local_client(tmp_path: Path) -> TestClient:
    application = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    return TestClient(
        application, base_url="http://127.0.0.1", headers={SECRET_HEADER: _TEST_SECRET}
    )


def _post_corrupt_convert(client: TestClient, **extra_headers) -> object:
    """POST a corrupt .docx to /api/convert; 400 proves auth+limits passed."""
    return client.post(
        "/api/convert",
        files={"file": ("paper.docx", b"not a real docx", _DOCX_MIME)},
        data={"journal": "revtex4-2"},
        headers=extra_headers or None,
    )


# --------------------------------------------------------------------------- #
# Served page: banner + Export-panel hiding
# --------------------------------------------------------------------------- #


def test_demo_page_carries_banner_flag_and_secret_wrapper(tmp_path):
    html = _demo_client(tmp_path).get("/").text
    assert "Public demo" in html
    assert "window.LATEXTIFY_DEMO=true" in html
    assert "install LaTeXtify" in html  # points visitors at the private local tool
    # The CSRF secret wrapper must still be injected on top of the banner.
    assert SECRET_HEADER in html


def test_local_page_has_no_demo_banner(tmp_path):
    html = _local_client(tmp_path).get("/").text
    assert "Public demo" not in html
    assert "LATEXTIFY_DEMO" not in html


def test_inject_demo_banner_lands_inside_body():
    out = inject_demo_banner("<html><body><main>x</main></body></html>")
    assert out.index("<body>") < out.index("demo-banner") < out.index("<main>")


# --------------------------------------------------------------------------- #
# Auth posture: same-origin + secret instead of loopback-only
# --------------------------------------------------------------------------- #


def test_demo_accepts_public_host_with_secret(tmp_path):
    # Locally this Host would be a hard 403; in demo mode the request reaches
    # the handler (400 = corrupt docx, i.e. auth passed).
    response = _post_corrupt_convert(_demo_client(tmp_path))
    assert response.status_code == 400


def test_demo_still_requires_the_secret(tmp_path):
    response = _post_corrupt_convert(_demo_client(tmp_path, secret=False))
    assert response.status_code == 403


def test_demo_rejects_cross_origin_requests(tmp_path):
    response = _post_corrupt_convert(
        _demo_client(tmp_path), Origin="https://evil.example.com"
    )
    assert response.status_code == 403


def test_demo_allows_same_origin_requests(tmp_path):
    response = _post_corrupt_convert(
        _demo_client(tmp_path), Origin=f"https://{_PUBLIC_HOST}"
    )
    assert response.status_code == 400  # corrupt docx, not forbidden


def test_local_mode_still_rejects_public_hosts(tmp_path):
    """Regression: demo mode must not loosen the local tool's loopback check."""
    application = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    client = TestClient(
        application, base_url=f"https://{_PUBLIC_HOST}", headers={SECRET_HEADER: _TEST_SECRET}
    )
    assert _post_corrupt_convert(client).status_code == 403


# --------------------------------------------------------------------------- #
# Server-filesystem endpoints are disabled
# --------------------------------------------------------------------------- #


def test_demo_pick_folder_is_403(tmp_path):
    response = _demo_client(tmp_path).post("/api/pick-folder")
    assert response.status_code == 403
    assert "disabled in the hosted demo" in response.json()["detail"]


def test_demo_export_is_403(tmp_path):
    response = _demo_client(tmp_path).post(
        "/api/export",
        json={"export_token": "whatever", "export_dir": "/tmp/x", "export_types": ["zip"]},
    )
    assert response.status_code == 403
    assert "disabled in the hosted demo" in response.json()["detail"]


def test_demo_convert_multi_rejects_inline_export_before_converting(tmp_path):
    response = _demo_client(tmp_path).post(
        "/api/convert-multi",
        files={"main": ("paper.docx", b"not a real docx", _DOCX_MIME)},
        data={"journal": "revtex4-2", "export_dir": "/tmp/steal", "export_types": ["zip"]},
    )
    assert response.status_code == 403
    assert "disabled in the hosted demo" in response.json()["detail"]


def test_local_pick_folder_still_works(tmp_path, monkeypatch):
    """Regression: the local Export flow is untouched by the demo guards."""
    monkeypatch.setattr(
        "latextify.gui.server.pick_folder_native", lambda *a, **k: "/home/user/papers"
    )
    response = _local_client(tmp_path).post("/api/pick-folder")
    assert response.status_code == 200
    assert response.json()["path"] == "/home/user/papers"


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


def test_rate_limiter_sliding_window():
    limiter = RateLimiter(max_requests=2, window_seconds=100.0)
    assert limiter.retry_after("a", now=0.0) == 0.0
    assert limiter.retry_after("a", now=10.0) == 0.0
    # Third request inside the window: denied, with the time until the oldest
    # hit ages out.
    assert limiter.retry_after("a", now=20.0) == 80.0
    # Other keys have their own window.
    assert limiter.retry_after("b", now=20.0) == 0.0
    # Once the oldest hit ages out, "a" may convert again.
    assert limiter.retry_after("a", now=101.0) == 0.0


def test_demo_convert_repeats_hit_429(tmp_path):
    app = _demo_app(tmp_path)
    app.state.rate_limiter = RateLimiter(max_requests=1, window_seconds=3600.0)
    client = _demo_client(tmp_path, app=app)

    assert _post_corrupt_convert(client).status_code == 400  # first: allowed
    denied = _post_corrupt_convert(client)
    assert denied.status_code == 429
    assert "Retry-After" in denied.headers
    assert "rate limit" in denied.json()["detail"]


def test_demo_rate_limit_is_per_client(tmp_path):
    app = _demo_app(tmp_path)
    app.state.rate_limiter = RateLimiter(max_requests=1, window_seconds=3600.0)
    client = _demo_client(tmp_path, app=app)

    first = _post_corrupt_convert(client, **{"X-Forwarded-For": "203.0.113.7"})
    assert first.status_code == 400
    denied = _post_corrupt_convert(client, **{"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
    assert denied.status_code == 429  # same first hop -> same budget
    other = _post_corrupt_convert(client, **{"X-Forwarded-For": "198.51.100.9"})
    assert other.status_code == 400  # different client -> own budget


def test_local_mode_has_no_rate_limit(tmp_path):
    client = _local_client(tmp_path)
    for _ in range(3):
        assert _post_corrupt_convert(client).status_code == 400


# --------------------------------------------------------------------------- #
# Upload cap
# --------------------------------------------------------------------------- #


def test_demo_upload_over_25mb_is_413(tmp_path):
    oversized = b"\0" * (25 * 1024 * 1024 + 1)
    response = _demo_client(tmp_path).post(
        "/api/convert",
        files={"file": ("big.docx", oversized, _DOCX_MIME)},
        data={"journal": "revtex4-2"},
    )
    assert response.status_code == 413
    assert "25 MB" in response.json()["detail"]


def test_local_upload_over_25mb_is_still_accepted(tmp_path):
    # Same payload against the local app streams fully (250 MB cap) and fails
    # only later as a corrupt docx -- proving the tighter cap is demo-only.
    oversized = b"\0" * (25 * 1024 * 1024 + 1)
    response = _local_client(tmp_path).post(
        "/api/convert",
        files={"file": ("big.docx", oversized, _DOCX_MIME)},
        data={"journal": "revtex4-2"},
    )
    assert response.status_code == 400
