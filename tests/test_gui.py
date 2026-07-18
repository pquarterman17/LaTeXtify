"""Tests for the local GUI server + `latextify gui` CLI command (plan item 19).

Uses FastAPI's TestClient (httpx-backed, already a project dependency) so no
real server binds during the test suite. Real Tectonic-backed tests follow
the `@pytest.mark.tectonic` + `_tectonic_available()` skip pattern already
established in tests/test_cli.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from latextify.cli import app
from latextify.compile.tectonic import find_tectonic
from latextify.gui.guard import SECRET_HEADER
from latextify.gui.server import create_app

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"
CLEAN_DOCX = FIXTURES / "clean.docx"

_SAMPLE_BIB = (
    b"@article{k, title={A Title}, author={Doe, Jane}, journal={Phys. Rev. B}, "
    b"year={2020}, doi={10.1/x}}\n"
)

runner = CliRunner()

# Deterministic secret + loopback Host/secret header so mutating /api/* requests
# pass the audit-item-4 guard (require_gui_auth) under TestClient, which
# otherwise sends Host: testserver and no secret.
_TEST_SECRET = "test-gui-secret"


def _client_for(application) -> TestClient:
    return TestClient(
        application,
        base_url="http://127.0.0.1",
        headers={SECRET_HEADER: _TEST_SECRET},
    )


def _client(tmp_path: Path) -> TestClient:
    application = create_app(workdir=tmp_path / "gui-workdir", gui_secret=_TEST_SECRET)
    return _client_for(application)


def _ui_text(client: TestClient) -> str:
    """The full DOM contract: served index + the split static JS files.

    The buildless page was split into index.html + app.js + review.js (+
    style.css); assertions about endpoint wiring and JS behavior span all of
    them, so contract smoke tests grep this concatenation.
    """
    return (
        client.get("/").text
        + client.get("/static/app.js").text
        + client.get("/static/review.js").text
    )


# --------------------------------------------------------------------------- #
# GET /
# --------------------------------------------------------------------------- #


def test_index_serves_the_static_page(tmp_path):
    client = _client(tmp_path)
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()
    assert "LaTeXtify" in response.text


def test_index_wires_the_multifile_ui(tmp_path):
    """The static page drives /api/convert-multi with a multi-file dropzone,
    per-file role dropdowns, and the four option toggles (buildless, so this
    is a DOM-contract smoke test rather than a JS execution test)."""
    html = _ui_text(_client(tmp_path))

    assert "/api/convert-multi" in html
    assert "multiple" in html  # multi-file input
    assert 'id="filelist"' in html  # per-file role table
    assert 'id="crossref-email"' in html
    for toggle in ("opt-pdf", "opt-combine", "opt-zip", "opt-audit", "opt-si1col", "opt-checkrefs"):
        assert f'id="{toggle}"' in html, toggle


def test_index_citation_styles_have_labels(tmp_path):
    """The page's JS labels citation styles with human-readable examples."""
    html = _ui_text(_client(tmp_path))
    assert "author–year — (Doe, 2020)" in html


def test_split_static_assets_are_served(tmp_path):
    """The split page assets come back from /static with sane content types."""
    client = _client(tmp_path)
    css = client.get("/static/style.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    for name in ("app.js", "review.js"):
        resp = client.get(f"/static/{name}")
        assert resp.status_code == 200, name
        assert "javascript" in resp.headers["content-type"], name
    # The served index references exactly these assets.
    html = client.get("/").text
    assert '/static/style.css' in html
    assert '/static/app.js' in html and '/static/review.js' in html


def test_options_are_grouped_and_every_toggle_explained(tmp_path):
    """Options cluster into labeled groups (plan item 2) and every checkbox
    row carries a substantive hover explanation."""
    html = _client(tmp_path).get("/").text
    for legend in ("Conversion", "Outputs", "Online checks"):
        assert f"<legend>{legend}</legend>" in html, legend
    for opt in (
        "opt-pdf", "opt-combine", "opt-si1col", "opt-zip",
        "opt-nofigs", "opt-audit", "opt-checkrefs",
    ):
        pattern = rf'<label class="checkbox-row" title="[^"]{{30,}}"><input id="{opt}"'
        assert re.search(pattern, html), f"{opt} lacks a tooltip"


def test_dropzone_advertises_accepted_formats(tmp_path):
    """The dropzone text + file-picker filter are built from the same accept
    lists role detection uses (plan item 5)."""
    js = _client(tmp_path).get("/static/app.js").text
    assert "dropzone-text" in js
    for advertised in ("webp", "eps", "svg", "ris"):
        assert advertised in js, advertised


def test_input_aware_toggle_wiring_present(tmp_path):
    """Supplement-dependent toggles disable without a supplement and
    exclude-figures warns over staged figure files (plan item 3) — the page
    is buildless, so this pins the wiring rather than executing it."""
    client = _client(tmp_path)
    assert 'id="nofigs-warning"' in client.get("/").text
    js = client.get("/static/app.js").text
    assert "updateOptionState" in js
    assert "Add a file with the Supplement role to enable." in js
    # opt-nofigs invalidates a stale preview like every other option toggle.
    assert re.search(r"optNoFigs[\s\S]{0,120}invalidatePreview", js)


def test_index_wires_the_review_panel(tmp_path):
    """The static page carries the reference-review panel + apply wiring."""
    html = _ui_text(_client(tmp_path))
    assert 'id="review-panel"' in html
    assert 'id="review-cards"' in html
    assert 'id="apply-btn"' in html
    assert "/api/apply-corrections" in html


def test_index_wires_the_export_panel(tmp_path):
    """The Export panel offers a Browse button (drives /api/pick-folder), a
    destination field, one checkbox per exportable artifact type, and a
    dedicated Export button that posts to /api/export (preview-then-export)."""
    html = _ui_text(_client(tmp_path))

    assert "/api/pick-folder" in html
    assert "/api/export" in html
    assert 'id="browse-btn"' in html
    assert 'id="export-dir"' in html
    assert 'id="export-btn"' in html
    for box in ("exp-project", "exp-main_pdf", "exp-combined_pdf", "exp-audit_pdf", "exp-zip"):
        assert f'id="{box}"' in html, box


# --------------------------------------------------------------------------- #
# GET /api/journals
# --------------------------------------------------------------------------- #


def test_journals_endpoint_lists_all_registered_journals_with_modes(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/journals")

    assert response.status_code == 200
    body = response.json()
    names = {entry["name"] for entry in body}
    assert {"revtex4-2", "elsarticle", "ieeetran", "sn-jnl", "aps-prl", "aip-apl"} <= names

    by_name = {entry["name"]: entry["modes"] for entry in body}
    assert by_name["revtex4-2"] == ["numeric"]
    assert set(by_name["elsarticle"]) == {"numeric", "authoryear"}
    assert by_name["ieeetran"] == ["numeric"]

    # Every entry carries a human-readable display name; variants get proper ones.
    display = {entry["name"]: entry["display_name"] for entry in body}
    assert display["revtex4-2"].startswith("American Physical Society")
    assert "Physical Review Letters" in display["aps-prl"]
    assert all(entry["display_name"] for entry in body)


# --------------------------------------------------------------------------- #
# POST /api/convert
# --------------------------------------------------------------------------- #


def test_convert_endpoint_returns_success_warnings_and_report(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert",
            files={
                "file": (
                    "figures.docx",
                    fh,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"journal": "revtex4-2"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["warnings"], list)
    assert "# Conversion Report" in body["report_md"]
    assert body["pdf_url"] is None

    output_dir = Path(body["output_dir"])
    assert output_dir.is_dir()
    assert (output_dir / "main.tex").is_file()
    assert (output_dir / "report.md").is_file()


def test_convert_endpoint_sanitizes_uploaded_filename(tmp_path):
    """A hostile filename must never be interpreted as a path -- only its
    basename is used for the on-disk upload."""
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert",
            files={"file": ("../../evil.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2"},
        )

    assert response.status_code == 200, response.text
    # The whole gui-workdir tree stays inside tmp_path -- nothing escaped it.
    for path in (tmp_path / "gui-workdir").rglob("*"):
        assert str(tmp_path) in str(path.resolve())


def test_convert_endpoint_invalid_journal_returns_4xx_with_clear_detail(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert",
            files={"file": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "no-such-journal"},
        )

    assert 400 <= response.status_code < 500
    assert "no-such-journal" in response.json()["detail"]


def test_convert_endpoint_unsupported_citation_style_returns_4xx(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert",
            files={"file": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "ieeetran", "citation_style": "authoryear"},
        )

    assert 400 <= response.status_code < 500
    detail = response.json()["detail"]
    assert "ieeetran" in detail
    assert "authoryear" in detail


def test_convert_endpoint_corrupt_docx_returns_4xx_not_500(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/convert",
        files={"file": ("bogus.docx", b"not a docx", "application/octet-stream")},
        data={"journal": "revtex4-2"},
    )

    assert 400 <= response.status_code < 500


# --------------------------------------------------------------------------- #
# POST /api/convert-multi -- main + supplement + figures + .bib + options
# --------------------------------------------------------------------------- #


def test_convert_multi_main_only_succeeds_without_pdf(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert "# Conversion Report" in body["report_md"]
    # No optional artifacts were requested.
    assert body["pdf_url"] is None
    assert body["combined_pdf_url"] is None
    assert body["audit_pdf_url"] is None
    assert body["zip_url"] is None
    assert (Path(body["output_dir"]) / "main.tex").is_file()


def test_convert_multi_exclude_figures_emits_text_only(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false", "exclude_figures": "true"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    body_tex = (Path(body["output_dir"]) / "generated" / "body.tex").read_text(encoding="utf-8")
    assert "\\includegraphics" not in body_tex
    assert "%%FIGURE:" not in body_tex


def test_convert_multi_accepts_a_references_bib(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={
                "main": ("figures.docx", fh, "application/octet-stream"),
                "references": ("lib.bib", _SAMPLE_BIB, "text/plain"),
            },
            data={"journal": "revtex4-2", "pdf": "false"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True


def test_convert_multi_combine_requires_supplement(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "combine": "true", "pdf": "true"},
        )

    assert response.status_code == 400
    assert "supplement" in response.json()["detail"]


def test_convert_multi_combine_requires_pdf(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as main_fh, CLEAN_DOCX.open("rb") as si_fh:
        response = client.post(
            "/api/convert-multi",
            files={
                "main": ("figures.docx", main_fh, "application/octet-stream"),
                "supplement": ("clean.docx", si_fh, "application/octet-stream"),
            },
            data={"journal": "revtex4-2", "combine": "true", "pdf": "false"},
        )

    assert response.status_code == 400
    assert "pdf" in response.json()["detail"]


def test_convert_multi_figure_count_mismatch_is_400(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as main_fh:
        response = client.post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", main_fh.read(), "application/octet-stream")),
                ("figures", ("fig1.png", b"\x89PNG\r\n", "image/png")),
            ],
            # one figure file but no figure_numbers -> mismatch
            data={"journal": "revtex4-2", "pdf": "false"},
        )

    assert response.status_code == 400
    assert "figure_numbers" in response.json()["detail"]


def test_convert_multi_want_zip_streams_a_project_zip(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false", "want_zip": "true"},
        )

    assert response.status_code == 200, response.text
    zip_url = response.json()["zip_url"]
    assert zip_url is not None and zip_url.startswith("/api/zip/")

    zip_response = client.get(zip_url)
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"
    assert zip_response.content[:2] == b"PK"  # zip local-file-header magic


def test_zip_endpoint_unknown_token_is_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/zip/does-not-exist").status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/convert-multi -- export selected artifacts to a chosen folder
# --------------------------------------------------------------------------- #


def test_convert_multi_exports_selected_artifacts_to_a_folder(tmp_path):
    client = _client(tmp_path)
    export_dir = tmp_path / "chosen-folder"
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={
                "journal": "revtex4-2",
                "pdf": "false",
                "export_dir": str(export_dir),
                # httpx sends a list value as repeated form fields.
                "export_types": ["project", "zip"],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exported_to"] == str(export_dir)
    # The project tree was copied under the chosen folder, and a zip was written.
    project_copy = export_dir / Path(body["output_dir"]).name
    assert (project_copy / "main.tex").is_file()
    assert (export_dir / "latextify-project.zip").is_file()
    assert any("project" in name for name in body["exported"])
    assert "latextify-project.zip" in body["exported"]


def test_convert_multi_export_warns_on_unproduced_artifact(tmp_path):
    """Requesting an artifact that was not produced (combined PDF without a
    combine step) is a warning, not a fatal error -- the export still runs."""
    client = _client(tmp_path)
    export_dir = tmp_path / "partial-export"
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={
                "journal": "revtex4-2",
                "pdf": "false",
                "export_dir": str(export_dir),
                "export_types": ["project", "combined_pdf"],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exported_to"] == str(export_dir)
    # project still copied...
    assert (export_dir / Path(body["output_dir"]).name / "main.tex").is_file()
    # ...but the unproduced combined_pdf surfaced as a warning.
    assert any("combined_pdf" in w for w in body["warnings"])


def test_convert_multi_without_export_dir_does_not_export(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exported_to"] is None
    assert body["exported"] == []


# --------------------------------------------------------------------------- #
# POST /api/export -- preview-then-export (copy a prior run's artifacts)
# --------------------------------------------------------------------------- #


def test_convert_multi_returns_an_export_token(tmp_path):
    """Every convert-multi run hands back a token for a later /api/export."""
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["export_token"]


def test_export_endpoint_copies_previewed_artifacts(tmp_path):
    """The two-step flow: preview (no export), then export that result via token."""
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        preview = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    # Preview alone never writes to a destination folder.
    assert body["exported_to"] is None
    token = body["export_token"]

    export_dir = tmp_path / "later-folder"
    export = client.post(
        "/api/export",
        json={
            "export_token": token,
            "export_dir": str(export_dir),
            "export_types": ["project", "zip"],
        },
    )
    assert export.status_code == 200, export.text
    exported = export.json()
    assert exported["exported_to"] == str(export_dir)
    assert (export_dir / Path(body["output_dir"]).name / "main.tex").is_file()
    assert (export_dir / "latextify-project.zip").is_file()


def test_export_endpoint_unknown_token_is_404(tmp_path):
    client = _client(tmp_path)
    response = client.post(
        "/api/export",
        json={
            "export_token": "does-not-exist",
            "export_dir": str(tmp_path),
            "export_types": ["project"],
        },
    )
    assert response.status_code == 404
    assert "token" in response.json()["detail"]


def test_export_endpoint_blank_folder_is_400(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        token = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        ).json()["export_token"]

    response = client.post(
        "/api/export",
        json={"export_token": token, "export_dir": "   ", "export_types": ["project"]},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# POST /api/pick-folder -- native dialog on the server host
# --------------------------------------------------------------------------- #


def test_pick_folder_returns_the_chosen_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "latextify.gui.server.pick_folder_native", lambda *a, **k: "/home/user/papers"
    )
    client = _client(tmp_path)
    response = client.post("/api/pick-folder")

    assert response.status_code == 200
    assert response.json()["path"] == "/home/user/papers"


def test_pick_folder_returns_empty_when_cancelled_or_headless(tmp_path, monkeypatch):
    # Simulate a cancel / headless host: the picker returns "".
    monkeypatch.setattr("latextify.gui.server.pick_folder_native", lambda *a, **k: "")
    client = _client(tmp_path)
    response = client.post("/api/pick-folder")

    assert response.status_code == 200
    assert response.json()["path"] == ""


def test_convert_multi_invalid_journal_is_400(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "no-such-journal", "pdf": "false"},
        )
    assert 400 <= response.status_code < 500
    assert "no-such-journal" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# GET /api/pdf/{token} -- server-issued tokens only, never a filesystem path
# --------------------------------------------------------------------------- #


def test_pdf_endpoint_unknown_token_is_404(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/pdf/does-not-exist")
    assert response.status_code == 404


def test_pdf_endpoint_path_traversal_attempt_is_404(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/pdf/" + "..%2F..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404


def test_pdf_endpoint_without_pdf_flag_convert_never_issues_a_token(tmp_path):
    """A plain (non --pdf) convert must not populate any pdf token at all."""
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        client.post(
            "/api/convert",
            files={"file": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2"},
        )

    app = client.app
    assert app.state.pdf_tokens == {}


def _tectonic_available() -> bool:
    # Detection only -- must NOT download at collection time: anonymous
    # GitHub API calls from CI runners hit rate limits, and unit jobs
    # deselect tectonic tests anyway. ensure_tectonic() still runs (and
    # downloads if needed) inside the marked tests themselves; CI's
    # integration job pre-fetches the binary before pytest.
    return find_tectonic() is not None


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_convert_endpoint_with_pdf_flag_streams_a_real_pdf(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as fh:
        response = client.post(
            "/api/convert",
            files={"file": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "true"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["pdf_url"] is not None
    assert body["pdf_url"].startswith("/api/pdf/")

    pdf_response = client.get(body["pdf_url"])
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content[:5] == b"%PDF-"

    # report.md is rewritten with compile diagnostics once --pdf runs.
    report_text = (Path(body["output_dir"]) / "report.md").read_text(encoding="utf-8")
    assert "## Compilation" in report_text


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_convert_multi_pdf_combine_audit_zip_end_to_end(tmp_path):
    client = _client(tmp_path)
    with FIGURES_DOCX.open("rb") as main_fh, CLEAN_DOCX.open("rb") as si_fh:
        response = client.post(
            "/api/convert-multi",
            files={
                "main": ("figures.docx", main_fh, "application/octet-stream"),
                "supplement": ("clean.docx", si_fh, "application/octet-stream"),
            },
            data={
                "journal": "revtex4-2",
                "pdf": "true",
                "combine": "true",
                "equation_audit": "true",
                "want_zip": "true",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    # Every requested artifact minted a token.
    for key in ("pdf_url", "supplement_pdf_url", "combined_pdf_url", "audit_pdf_url", "zip_url"):
        assert body[key] is not None, key

    combined = client.get(body["combined_pdf_url"])
    assert combined.status_code == 200
    assert combined.headers["content-type"] == "application/pdf"
    assert combined.content[:5] == b"%PDF-"

    assert client.get(body["zip_url"]).content[:2] == b"PK"


# --------------------------------------------------------------------------- #
# `latextify gui` CLI command
# --------------------------------------------------------------------------- #


def test_gui_command_without_optional_deps_prints_actionable_error(monkeypatch):
    """Simulate the 'gui' extra not being installed: block the imports the
    command performs lazily and confirm it fails cleanly (no traceback, an
    install hint) rather than crashing every other CLI invocation."""
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    monkeypatch.setitem(sys.modules, "latextify.gui.server", None)

    result = runner.invoke(app, ["gui", "--no-browser"])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output
    assert "install" in result.output.lower()
    assert "latextify[gui]" in result.output


def test_gui_command_help_documents_flags():
    # Typer renders help through Rich, which on CI terminals injects ANSI
    # styling and wraps to a narrow width, breaking naive substring asserts.
    # Force plain, wide output for a stable assertion surface.
    result = runner.invoke(
        app, ["gui", "--help"], env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    )

    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--port" in plain
    assert "--no-browser" in plain
    assert "--workdir" in plain


# --------------------------------------------------------------------------- #
# Reference review + /api/apply-corrections
# --------------------------------------------------------------------------- #

ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"


def _flag_first_year(monkeypatch, *, canonical_year="1999"):
    """Monkeypatch the validator so the first reference is flagged (year mismatch).

    Avoids real network: emit_project's validate_references is replaced with a
    stub that flags entries[0] with a year correction (canonical_entry carries
    the new year) and marks the rest verified.
    """
    from dataclasses import replace

    from latextify.emit import project as project_mod
    from latextify.model.validate import FieldCheck, ValidationRecord, ValidationReport

    def fake_validate(entries, client, **kwargs):
        first = entries[0]
        canonical = replace(first, year=canonical_year)
        flagged = ValidationRecord(
            key=first.key, status="mismatch", doi=first.doi or "10.1/x",
            checks=(FieldCheck(field="year", ours=first.year or "?",
                               canonical=canonical_year, ok=False),),
            canonical_entry=canonical,
        )
        rest = tuple(
            ValidationRecord(key=e.key, status="verified", doi=e.doi) for e in entries[1:]
        )
        return ValidationReport(records=(flagged, *rest))

    monkeypatch.setattr(project_mod, "validate_references", fake_validate)


def _convert_with_check(client):
    with ZOTERO_DOCX.open("rb") as fh:
        return client.post(
            "/api/convert-multi",
            files={"main": ("zotero_cited.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false", "check_references": "true"},
        )


def test_convert_multi_returns_structured_validation(tmp_path, monkeypatch):
    _flag_first_year(monkeypatch)
    resp = _convert_with_check(_client(tmp_path))
    assert resp.status_code == 200, resp.text
    val = resp.json()["validation"]
    assert val is not None
    assert val["flagged"] == 1
    rec = val["records"][0]
    assert rec["status"] == "mismatch"
    assert rec["entry"]["year"]  # current entry exposed for editing
    assert rec["canonical"]["year"] == "1999"  # crossref version exposed
    assert any(p["field"] == "year" for p in rec["problems"])


def test_convert_multi_no_check_has_null_validation(tmp_path):
    with ZOTERO_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files={"main": ("zotero_cited.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["validation"] is None


def test_apply_corrections_approve_rewrites_bib(tmp_path, monkeypatch):
    _flag_first_year(monkeypatch)
    client = _client(tmp_path)
    body = _convert_with_check(client).json()
    key = body["validation"]["records"][0]["key"]

    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": body["export_token"],
              "decisions": [{"key": key, "action": "approve"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 1

    bibs = list((tmp_path / "gui-workdir").rglob("references.bib"))
    assert bibs, "references.bib not found under workdir"
    assert "year = {1999}" in bibs[0].read_text(encoding="utf-8")


def test_apply_corrections_edit_uses_posted_fields(tmp_path, monkeypatch):
    _flag_first_year(monkeypatch)
    client = _client(tmp_path)
    body = _convert_with_check(client).json()
    rec = body["validation"]["records"][0]
    edited = dict(rec["entry"])
    edited["year"] = "2042"
    edited["title"] = "Manually Corrected Title"

    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": body["export_token"],
              "decisions": [{"key": rec["key"], "action": "edit", "entry": edited}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 1

    bibs = list((tmp_path / "gui-workdir").rglob("references.bib"))
    text = bibs[0].read_text(encoding="utf-8")
    assert "year = {2042}" in text
    assert "Manually Corrected Title" in text


def test_apply_corrections_deny_changes_nothing(tmp_path, monkeypatch):
    _flag_first_year(monkeypatch)
    client = _client(tmp_path)
    body = _convert_with_check(client).json()
    key = body["validation"]["records"][0]["key"]
    bibs = list((tmp_path / "gui-workdir").rglob("references.bib"))
    before = bibs[0].read_text(encoding="utf-8")

    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": body["export_token"],
              "decisions": [{"key": key, "action": "deny"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == 0
    assert bibs[0].read_text(encoding="utf-8") == before


def test_apply_corrections_unknown_token_404(tmp_path):
    resp = _client(tmp_path).post(
        "/api/apply-corrections",
        json={"export_token": "deadbeef", "decisions": []},
    )
    assert resp.status_code == 404


def test_apply_corrections_without_check_is_400(tmp_path):
    # A conversion run WITHOUT --check-references has no validation to correct.
    client = _client(tmp_path)
    with ZOTERO_DOCX.open("rb") as fh:
        body = client.post(
            "/api/convert-multi",
            files={"main": ("zotero_cited.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        ).json()
    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": body["export_token"], "decisions": []},
    )
    assert resp.status_code == 400


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_apply_corrections_recompiles_pdf(tmp_path, monkeypatch):
    # A run that compiled a PDF must, after applying a correction, return a fresh
    # (recompiled) PDF token reflecting the corrected references.bib.
    _flag_first_year(monkeypatch)
    client = _client(tmp_path)
    with ZOTERO_DOCX.open("rb") as fh:
        body = client.post(
            "/api/convert-multi",
            files={"main": ("zotero_cited.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "true", "check_references": "true"},
        ).json()
    key = body["validation"]["records"][0]["key"]

    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": body["export_token"],
              "decisions": [{"key": key, "action": "approve"}]},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["applied"] == 1
    assert payload["success"] is True
    assert payload["pdf_url"] is not None
    # The freshly-issued token streams a real PDF.
    pdf = client.get(payload["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


# --------------------------------------------------------------------------- #
# Supplement export + honest compile success (audit item 6)
# --------------------------------------------------------------------------- #


def test_export_artifacts_can_copy_supplement_pdf(tmp_path):
    from latextify.gui.server import _export_artifacts

    src = tmp_path / "supplement.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    dest_dir = tmp_path / "out"
    dest, exported, warnings = _export_artifacts(
        str(dest_dir), {"supplement_pdf"},
        output_dir=tmp_path / "proj",
        produced={"project": tmp_path / "proj", "supplement_pdf": src},
    )
    assert "supplement.pdf" in exported
    assert (dest_dir / "supplement.pdf").is_file()
    assert warnings == []


def test_supplement_pdf_is_exportable_type():
    from latextify.gui.server import _EXPORTABLE

    assert "supplement_pdf" in _EXPORTABLE


def test_convert_multi_no_pdf_has_null_compile_success(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        body = _client(tmp_path).post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        ).json()
    # No compile requested: overall success True, per-document outcomes are None.
    assert body["success"] is True
    assert body["main_compile_success"] is None
    assert body["supplement_compile_success"] is None


# --------------------------------------------------------------------------- #
# Upload naming + validation (audit item 5)
# --------------------------------------------------------------------------- #


def test_main_must_be_docx(tmp_path):
    resp = _client(tmp_path).post(
        "/api/convert-multi",
        files={"main": ("paper.txt", b"not a docx", "text/plain")},
        data={"journal": "revtex4-2", "pdf": "false"},
    )
    assert resp.status_code == 400
    assert "docx" in resp.json()["detail"].lower()


def test_supplement_must_be_docx(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", fh.read(), "application/octet-stream")),
                ("supplement", ("si.pdf", b"%PDF-1.4", "application/pdf")),
            ],
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 400
    assert "supplement" in resp.json()["detail"].lower()


def test_references_must_be_bib_or_ris(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", fh.read(), "application/octet-stream")),
                ("references", ("lib.txt", b"junk", "text/plain")),
            ],
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 400
    assert "bib" in resp.json()["detail"].lower()


def test_figure_number_must_be_positive(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", fh.read(), "application/octet-stream")),
                ("figures", ("fig.png", b"\x89PNG\r\n", "image/png")),
            ],
            data={"journal": "revtex4-2", "pdf": "false", "figure_numbers": "0"},
        )
    assert resp.status_code == 400
    assert "positive" in resp.json()["detail"].lower()


def test_duplicate_figure_numbers_rejected(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", fh.read(), "application/octet-stream")),
                ("figures", ("a.png", b"\x89PNG\r\n", "image/png")),
                ("figures", ("b.png", b"\x89PNG\r\n", "image/png")),
            ],
            data={"journal": "revtex4-2", "pdf": "false",
                  "figure_numbers": ["1", "1"]},
        )
    assert resp.status_code == 400
    assert "unique" in resp.json()["detail"].lower()


def test_unsupported_figure_extension_rejected(tmp_path):
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("figures.docx", fh.read(), "application/octet-stream")),
                ("figures", ("evil.exe", b"MZ", "application/octet-stream")),
            ],
            data={"journal": "revtex4-2", "pdf": "false", "figure_numbers": "1"},
        )
    assert resp.status_code == 400
    assert "unsupported figure type" in resp.json()["detail"].lower()


def test_uppercase_docx_extension_accepted(tmp_path):
    # Extension check is case-insensitive; a valid manuscript still converts.
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files={"main": ("PAPER.DOCX", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 200, resp.text
    # Stored under the fixed server name regardless of the client's basename.
    mains = list((tmp_path / "gui-workdir").rglob("main.docx"))
    assert mains, "upload should be stored as main.docx"


def test_main_and_references_cannot_collide(tmp_path):
    # Same client basename for main and references must not overwrite each other.
    with FIGURES_DOCX.open("rb") as fh:
        resp = _client(tmp_path).post(
            "/api/convert-multi",
            files=[
                ("main", ("paper.docx", fh.read(), "application/octet-stream")),
                ("references", ("paper.bib", _SAMPLE_BIB, "text/plain")),
            ],
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 200, resp.text
    workdir = tmp_path / "gui-workdir"
    assert list(workdir.rglob("main.docx")), "main stored as main.docx"
    assert list(workdir.rglob("references.bib")), "references stored as references.bib"


# --------------------------------------------------------------------------- #
# Session TTL / cleanup / lifecycle (audit item 3)
# --------------------------------------------------------------------------- #


def test_expired_session_is_pruned_and_tokens_404(tmp_path):
    import time as _time

    from latextify.gui import server as srv
    from latextify.gui.server import create_app

    app = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    client = _client_for(app)
    with FIGURES_DOCX.open("rb") as fh:
        body = client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false", "want_zip": "true"},
        ).json()
    token, zip_url = body["export_token"], body["zip_url"]
    session_dir = app.state.export_sessions[token]["_session_dir"]
    assert session_dir.is_dir()
    assert client.get(zip_url).status_code == 200  # downloadable while live

    # Force every session past its TTL.
    srv._prune_sessions(app, now=_time.time() + srv._SESSION_TTL_SECONDS + 10)

    assert token not in app.state.export_sessions
    assert not session_dir.exists()          # on-disk directory removed
    assert client.get(zip_url).status_code == 404  # its token no longer resolves
    export = client.post(
        "/api/export",
        json={"export_token": token, "export_dir": str(tmp_path / "out"),
              "export_types": ["project"]},
    )
    assert export.status_code == 404


def test_touch_session_defers_expiry(tmp_path):
    from latextify.gui import server as srv
    from latextify.gui.server import _prune_sessions, _register_session, _touch_session, create_app

    app = create_app(workdir=tmp_path / "wd")
    d = tmp_path / "wd" / "s"
    d.mkdir(parents=True)
    _register_session(app, "t", {"output_dir": d}, session_dir=d, now=0.0)

    _touch_session(app.state.export_sessions["t"], now=1000.0)
    _prune_sessions(app, now=1000.0 + srv._SESSION_TTL_SECONDS - 1)
    assert "t" in app.state.export_sessions          # refreshed access keeps it alive
    _prune_sessions(app, now=1000.0 + srv._SESSION_TTL_SECONDS + 1)
    assert "t" not in app.state.export_sessions       # then it expires


def test_failed_conversion_leaves_no_session_dir(tmp_path):
    from latextify.gui.server import create_app

    wd = tmp_path / "wd"
    client = _client_for(create_app(workdir=wd, gui_secret=_TEST_SECRET))
    resp = client.post(
        "/api/convert-multi",
        files={"main": ("bogus.docx", b"not a real docx", "application/octet-stream")},
        data={"journal": "revtex4-2", "pdf": "false"},
    )
    assert resp.status_code == 400
    # The failed run's upload directory must not linger.
    assert [p for p in wd.iterdir() if p.is_dir()] == []


def test_register_session_lru_evicts_oldest(tmp_path):
    from latextify.gui import server as srv
    from latextify.gui.server import _register_session, create_app

    app = create_app(workdir=tmp_path / "wd")
    dirs = []
    for i in range(srv._MAX_SESSIONS + 3):
        d = tmp_path / "wd" / f"s{i}"
        d.mkdir(parents=True)
        dirs.append(d)
        _register_session(app, f"tok{i}", {"output_dir": d}, session_dir=d, now=float(i))

    sessions = app.state.export_sessions
    assert len(sessions) <= srv._MAX_SESSIONS
    assert "tok0" not in sessions        # oldest evicted
    assert not dirs[0].exists()          # and its directory removed
    assert f"tok{srv._MAX_SESSIONS + 2}" in sessions  # newest retained


def test_shutdown_removes_auto_created_root():
    from latextify.gui.server import create_app

    app = create_app()  # no workdir -> owns a temp root
    root = app.state.workdir
    assert root.is_dir()
    assert app.state.owns_root is True
    with TestClient(app):  # entering+exiting runs the lifespan (startup + shutdown)
        pass
    assert not root.exists()


def test_shutdown_preserves_caller_workdir(tmp_path):
    from latextify.gui.server import create_app

    wd = tmp_path / "persist"
    app = create_app(workdir=wd)
    assert app.state.owns_root is False
    with TestClient(app):
        pass
    assert wd.is_dir()  # a caller-supplied workdir is never deleted


# --------------------------------------------------------------------------- #
# Mutating-endpoint request protection (audit item 4)
# --------------------------------------------------------------------------- #


def _raw_client(tmp_path: Path, *, base_url: str = "http://127.0.0.1", headers=None) -> TestClient:
    """A client with explicit Host/headers so guard behavior can be exercised."""
    application = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    return TestClient(application, base_url=base_url, headers=headers or {})


def _multi_post(client: TestClient):
    with FIGURES_DOCX.open("rb") as fh:
        return client.post(
            "/api/convert-multi",
            files={"main": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )


def test_mutating_request_without_secret_is_forbidden(tmp_path):
    # Loopback Host but no secret header -> rejected before the upload is used.
    assert _multi_post(_raw_client(tmp_path)).status_code == 403


def test_mutating_request_with_wrong_secret_is_forbidden(tmp_path):
    client = _raw_client(tmp_path, headers={SECRET_HEADER: "nope"})
    assert _multi_post(client).status_code == 403


def test_mutating_request_with_secret_is_allowed(tmp_path):
    client = _raw_client(tmp_path, headers={SECRET_HEADER: _TEST_SECRET})
    assert _multi_post(client).status_code == 200  # guard passed, body ran


def test_non_loopback_host_is_forbidden(tmp_path):
    # Correct secret but a non-loopback Host (a DNS-rebinding attempt).
    client = _raw_client(
        tmp_path, base_url="http://evil.example", headers={SECRET_HEADER: _TEST_SECRET}
    )
    assert _multi_post(client).status_code == 403


def test_cross_origin_is_forbidden(tmp_path):
    client = _raw_client(
        tmp_path, headers={SECRET_HEADER: _TEST_SECRET, "Origin": "http://evil.com"}
    )
    assert _multi_post(client).status_code == 403


def test_loopback_origin_is_allowed(tmp_path):
    client = _raw_client(
        tmp_path, headers={SECRET_HEADER: _TEST_SECRET, "Origin": "http://127.0.0.1:8000"}
    )
    assert _multi_post(client).status_code == 200


def test_index_injects_secret_into_served_page(tmp_path):
    html = _client(tmp_path).get("/").text
    assert SECRET_HEADER in html
    assert _TEST_SECRET in html
    assert "window.fetch" in html


def test_static_index_file_carries_no_secret():
    from latextify.gui.server import _INDEX_HTML

    assert _TEST_SECRET not in _INDEX_HTML.read_text(encoding="utf-8")


def test_artifact_and_list_gets_need_no_secret(tmp_path):
    # GET endpoints are loopback/bearer capabilities, not guarded by the
    # mutation secret; a loopback GET without it still works.
    client = _raw_client(tmp_path)  # no secret header
    assert client.get("/api/journals").status_code == 200


# --------------------------------------------------------------------------- #
# Tech-debt fixes: single-convert session lifecycle + stale-zip invalidation
# --------------------------------------------------------------------------- #


def test_single_convert_registers_bounded_session(tmp_path):
    # /api/convert must register its session so it is TTL/LRU-prunable, rather
    # than leaking its dir + pdf token forever (finding 3).
    app = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    client = _client_for(app)
    with FIGURES_DOCX.open("rb") as fh:
        resp = client.post(
            "/api/convert",
            files={"file": ("figures.docx", fh, "application/octet-stream")},
            data={"journal": "revtex4-2", "pdf": "false"},
        )
    assert resp.status_code == 200, resp.text
    assert len(app.state.export_sessions) == 1  # registered -> prunable, not leaked


def test_failed_single_convert_leaves_no_session_dir(tmp_path):
    # A failed /api/convert must not leave the uploaded manuscript behind (finding 3).
    wd = tmp_path / "wd"
    client = _client_for(create_app(workdir=wd, gui_secret=_TEST_SECRET))
    resp = client.post(
        "/api/convert",
        files={"file": ("bogus.docx", b"not a real docx", "application/octet-stream")},
        data={"journal": "revtex4-2", "pdf": "false"},
    )
    assert resp.status_code == 400
    assert [p for p in wd.iterdir() if p.is_dir()] == []


def test_apply_corrections_invalidates_stale_zip(tmp_path, monkeypatch):
    # After corrections rewrite references.bib, the project .zip snapshot built
    # at convert time is stale; it must be dropped so export rebuilds a fresh
    # archive from the corrected output_dir (finding 2).
    _flag_first_year(monkeypatch)
    app = create_app(workdir=tmp_path / "wd", gui_secret=_TEST_SECRET)
    client = _client_for(app)
    with ZOTERO_DOCX.open("rb") as fh:
        body = client.post(
            "/api/convert-multi",
            files={"main": ("zotero_cited.docx", fh, "application/octet-stream")},
            data={
                "journal": "revtex4-2",
                "pdf": "false",
                "check_references": "true",
                "want_zip": "true",
            },
        ).json()
    token = body["export_token"]
    produced = app.state.export_sessions[token]["produced"]
    assert "zip" in produced  # convert built a snapshot

    key = body["validation"]["records"][0]["key"]
    resp = client.post(
        "/api/apply-corrections",
        json={"export_token": token, "decisions": [{"key": key, "action": "approve"}]},
    )
    assert resp.status_code == 200, resp.text
    assert "zip" not in produced  # stale snapshot invalidated -> export rebuilds fresh
