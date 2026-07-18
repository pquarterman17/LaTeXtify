"""Single-manuscript upload-processing POST routes (extracted from
``server.py`` to keep that module under its size-ratchet pin).

Two routes, both taking one manuscript upload and handing back a download
token for the file produced -- the same session/token pattern
``server.py``'s convert routes use (session bookkeeping lives in
:mod:`latextify.gui.downloads`, whose GET download routes stream the result
back by token):

    POST /api/clean-docx      sanitize an uploaded .docx -> token + CleanReport
    POST /api/export-format   export an uploaded manuscript to a single
                               self-contained HTML file or plain Markdown
                               file -> token + ExportResult summary

:func:`register_upload_routes` attaches both to an app, mirroring
:func:`latextify.gui.downloads.register_download_routes`. Both routes carry
the same guard as every other mutating ``/api/*`` endpoint
(``require_gui_auth`` + ``require_demo_rate_limit``, see ``server.py``'s
module docstring Security section).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile

from latextify.emit.alt_formats import export_html, export_markdown
from latextify.gui.demo import require_demo_rate_limit
from latextify.gui.downloads import _issue_token, _register_session, _rmtree
from latextify.gui.guard import require_gui_auth
from latextify.gui.schemas import AltExportResponse, CleanDocxResponse
from latextify.gui.upload_utils import _ALLOWED_MANUSCRIPT_EXTS, _lower_ext, _stream_upload
from latextify.ingest.docx_clean import sanitize_docx

#: format name -> exporter, and format name -> the extension it writes.
_ALT_EXPORTERS = {"html": export_html, "markdown": export_markdown}
_ALT_EXTENSIONS = {"html": ".html", "markdown": ".md"}


def register_upload_routes(app: FastAPI, *, root: Path, max_upload_bytes: int) -> None:
    """Attach ``POST /api/clean-docx`` and ``POST /api/export-format`` to ``app``.

    Args:
        root: parent directory each run's per-session working directory is
            created under -- the same ``root`` :func:`latextify.gui.server.
            create_app` passes to its own upload routes.
        max_upload_bytes: per-file size cap enforced while streaming the
            upload (demo-mode-lowered or the default, chosen by the caller).
    """

    @app.post(
        "/api/clean-docx",
        response_model=CleanDocxResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def clean_docx_endpoint(main: UploadFile = File(...)) -> CleanDocxResponse:
        """Sanitize an uploaded .docx: accept tracked changes, drop comments and
        hidden runs, strip docProps, scrub settings.xml rsids. Returns a download
        token for the cleaned copy plus a summary of what was removed."""
        if _lower_ext(main.filename) != "docx":
            raise HTTPException(status_code=400, detail="file must be a .docx")

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        src_path = upload_dir / "main.docx"
        dest_path = session_dir / "cleaned.docx"

        try:
            await _stream_upload(main, src_path, max_bytes=max_upload_bytes)
            report = sanitize_docx(src_path, dest_path)
        except ValueError as exc:
            _rmtree(session_dir)  # a failed clean must not leave the upload behind
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            _rmtree(session_dir)
            raise

        _register_session(
            app,
            uuid.uuid4().hex,
            {"output_dir": session_dir, "produced": {}},
            session_dir=session_dir,
            now=time.time(),
        )
        clean_url = f"/api/clean/{_issue_token(app.state.clean_tokens, dest_path)}"
        return CleanDocxResponse(
            clean_url=clean_url,
            tracked_changes_accepted=report.tracked_changes_accepted,
            comments_removed=report.comments_removed,
            hidden_runs_removed=report.hidden_runs_removed,
            docprops_stripped=report.docprops_stripped,
            rsids_scrubbed=report.rsids_scrubbed,
        )

    @app.post(
        "/api/export-format",
        response_model=AltExportResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def export_format_endpoint(
        main: UploadFile = File(...), fmt: str = Form(...)
    ) -> AltExportResponse:
        """Export an uploaded manuscript to a single self-contained ``.html``
        file or a plain ``.md`` file (FORMATS_AND_PRIVACY items 4-5's GUI
        action). ``fmt`` is ``"html"`` or ``"markdown"``.

        Reuses :mod:`latextify.emit.alt_formats` -- the same pipeline
        ``latextify export --format`` runs -- so the file produced here
        matches the CLI's output for the same manuscript exactly. See that
        module's docstring for what is (and is not) carried over from the
        LaTeX conversion path (no journal/columns/anonymize options; figures
        embedded or copied alongside instead of a LaTeX project tree).
        """
        fmt_norm = fmt.strip().lower()
        if fmt_norm not in _ALT_EXPORTERS:
            raise HTTPException(
                status_code=400, detail=f"unknown format '{fmt}' (expected html or markdown)"
            )
        ext = _lower_ext(main.filename)
        if ext not in _ALLOWED_MANUSCRIPT_EXTS:
            raise HTTPException(
                status_code=400,
                detail="manuscript must be one of: "
                + ", ".join("." + e for e in sorted(_ALLOWED_MANUSCRIPT_EXTS)),
            )

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        src_path = upload_dir / f"main.{ext}"
        dest_path = session_dir / f"export{_ALT_EXTENSIONS[fmt_norm]}"

        try:
            await _stream_upload(main, src_path, max_bytes=max_upload_bytes)
            result = _ALT_EXPORTERS[fmt_norm](src_path, dest_path)
        except ValueError as exc:
            _rmtree(session_dir)  # a failed export must not leave the upload behind
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            _rmtree(session_dir)
            raise

        _register_session(
            app,
            uuid.uuid4().hex,
            {"output_dir": session_dir, "produced": {}},
            session_dir=session_dir,
            now=time.time(),
        )
        download_url = f"/api/alt/{_issue_token(app.state.alt_tokens, result.output_path)}"
        return AltExportResponse(
            download_url=download_url,
            format=fmt_norm,
            figure_count=result.figure_count,
            citation_count=result.citation_count,
            warnings=[w.message for w in result.warnings],
        )
