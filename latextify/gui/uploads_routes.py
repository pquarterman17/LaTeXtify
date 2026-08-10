"""Single-manuscript upload-processing POST routes (extracted from
``server.py`` to keep that module under its size-ratchet pin).

Two routes, both taking one manuscript upload and handing back a download
token for the file produced -- the same session/token pattern
``server.py``'s convert routes use (session bookkeeping lives in
:mod:`latextify.gui.downloads`, whose GET download routes stream the result
back by token):

    POST /api/inspect         report an uploaded file's metadata (writes nothing)
    POST /api/clean-file      sanitize an uploaded file -> token + what was removed
    POST /api/export-format   export an uploaded manuscript to a single
                               self-contained HTML file or plain Markdown
                               file -> token + ExportResult summary

:func:`register_upload_routes` attaches all three to an app, mirroring
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
from latextify.gui.schemas import (
    AltExportResponse,
    CleanFileResponse,
    FindingModel,
    InspectResponse,
)
from latextify.gui.upload_utils import _ALLOWED_MANUSCRIPT_EXTS, _lower_ext, _stream_upload
from latextify.privacy import inspect_file, is_supported, sanitize_file, supported_extensions
from latextify.privacy.report import Finding

#: format name -> exporter, and format name -> the extension it writes.
_ALT_EXPORTERS = {"html": export_html, "markdown": export_markdown}
_ALT_EXTENSIONS = {"html": ".html", "markdown": ".md"}


def _as_models(findings: list[Finding]) -> list[FindingModel]:
    return [
        FindingModel(
            category=f.category,
            severity=f.severity,
            summary=f.summary,
            detail=f.detail,
            location=f.location,
            count=f.count,
            removable=f.removable,
        )
        for f in findings
    ]


def _reject_unsupported(filename: str | None) -> str:
    """Validate an upload's extension against the privacy registry.

    The accept list is READ FROM the registry rather than restated, so a
    format added there is reachable here automatically -- the dual-registration
    failure mode is a format the form accepts and the engine rejects.
    """
    name = Path(filename or "upload")
    if not is_supported(name):
        raise HTTPException(
            status_code=400,
            detail="unsupported file type; supported: " + ", ".join(supported_extensions()),
        )
    return name.suffix.lower()


def register_upload_routes(app: FastAPI, *, root: Path, max_upload_bytes: int) -> None:
    """Attach the inspect, clean and export upload routes to ``app``.

    Args:
        root: parent directory each run's per-session working directory is
            created under -- the same ``root`` :func:`latextify.gui.server.
            create_app` passes to its own upload routes.
        max_upload_bytes: per-file size cap enforced while streaming the
            upload (demo-mode-lowered or the default, chosen by the caller).
    """

    @app.post(
        "/api/inspect",
        response_model=InspectResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def inspect_endpoint(main: UploadFile = File(...)) -> InspectResponse:
        """Report what an uploaded file exposes, without producing anything.

        Deliberately issues no download token: this route writes nothing the
        user could take away, so the upload is deleted as soon as it is read.
        """
        ext = _reject_unsupported(main.filename)
        session_dir = root / uuid.uuid4().hex
        session_dir.mkdir(parents=True, exist_ok=True)
        src_path = session_dir / f"upload{ext}"

        try:
            await _stream_upload(main, src_path, max_bytes=max_upload_bytes)
            report = inspect_file(src_path)
            return InspectResponse(
                file_format=report.file_format,
                findings=_as_models(report.sorted_findings()),
                warnings=report.warnings,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            # Nothing here is downloadable, so the upload never outlives the request.
            _rmtree(session_dir)

    @app.post(
        "/api/clean-file",
        response_model=CleanFileResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def clean_file_endpoint(
        main: UploadFile = File(...), keep_notes: bool = Form(False)
    ) -> CleanFileResponse:
        """Sanitize an uploaded file of any supported format.

        Returns a download token for the cleaned copy, what was removed, and
        any residual risk the rewrite could not address (see
        :mod:`latextify.privacy`). Legacy .doc/.ppt/.xls are refused here with
        the same explanation the CLI gives -- use inspect on those instead.
        """
        ext = _reject_unsupported(main.filename)

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        src_path = upload_dir / f"main{ext}"
        dest_path = session_dir / f"cleaned{ext}"

        try:
            await _stream_upload(main, src_path, max_bytes=max_upload_bytes)
            report = sanitize_file(src_path, dest_path, keep_notes=keep_notes)
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
        return CleanFileResponse(
            clean_url=clean_url,
            file_format=report.file_format,
            removed=_as_models(report.sorted_removed()),
            warnings=report.warnings,
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
