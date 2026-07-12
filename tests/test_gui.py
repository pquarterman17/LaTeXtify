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
from latextify.gui.server import create_app

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"

runner = CliRunner()


def _client(tmp_path: Path) -> TestClient:
    application = create_app(workdir=tmp_path / "gui-workdir")
    return TestClient(application)


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


# --------------------------------------------------------------------------- #
# GET /api/journals
# --------------------------------------------------------------------------- #


def test_journals_endpoint_lists_all_registered_journals_with_modes(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/journals")

    assert response.status_code == 200
    body = response.json()
    names = {entry["name"] for entry in body}
    assert {"revtex4-2", "elsarticle", "ieeetran", "sn-jnl"} <= names

    by_name = {entry["name"]: entry["modes"] for entry in body}
    assert by_name["revtex4-2"] == ["numeric"]
    assert set(by_name["elsarticle"]) == {"numeric", "authoryear"}
    assert by_name["ieeetran"] == ["numeric"]


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
