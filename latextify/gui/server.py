"""Local web GUI: FastAPI app wrapping the conversion pipeline (plan item 19).

Buildless v1: a single self-contained ``static/index.html`` (vanilla JS, no
build step, no CDN) talks to three JSON/file endpoints. This module contains
no conversion logic of its own -- it only orchestrates calls into
:mod:`latextify.emit.project`, :mod:`latextify.compile.tectonic`, and
:mod:`latextify.templates.loader`, the same functions :mod:`latextify.cli`'s
``convert`` command calls.

Public surface
--------------
    create_app(*, workdir=None) -> FastAPI

Endpoints
---------
    GET  /                    the static single-page UI
    GET  /api/journals        [{name, modes}] for every registered journal
    POST /api/convert         multipart upload -> JSON result
    GET  /api/pdf/{token}     stream a compiled PDF (server-issued token only)

Security
--------
This module never chooses the bind address -- see :func:`latextify.cli.gui`,
which binds ``127.0.0.1`` only (uploaded manuscripts are private; this is a
local tool, not a hosted service).

The PDF endpoint never treats a URL path segment as a filesystem path: a
successful ``--pdf`` compile mints a random ``uuid4`` token mapped, server
side only, to the real compiled path (``app.state.pdf_tokens``). ``GET
/api/pdf/{token}`` does a dict lookup by that opaque token; an unknown or
tampered token is a 404, never a path traversal. Uploaded filenames are
stripped to their basename (see :func:`_safe_filename`) before touching disk,
and every upload is written under a fresh per-session subdirectory of
``workdir`` that this module creates -- never a client-supplied path.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader
from latextify.templates.loader import ManifestError

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"


class JournalInfo(BaseModel):
    """One entry of ``GET /api/journals``."""

    name: str
    modes: list[str]


class ConvertResponse(BaseModel):
    """Body of ``POST /api/convert``."""

    output_dir: str
    warnings: list[str]
    report_md: str
    success: bool
    pdf_url: str | None = None


def _safe_filename(name: str | None) -> str:
    """Strip any directory components from a client-supplied filename.

    ``UploadFile.filename`` is attacker-controlled. The file is always
    written under a fresh per-session directory this module creates (never a
    path built from the filename itself), so this is defense in depth rather
    than the only guard -- but a bare basename keeps the on-disk name
    predictable and stops something like ``"../../evil.docx"`` from ever
    being interpreted as a relative path by anything downstream.
    """
    if not name:
        return "upload.docx"
    candidate = Path(name).name
    return candidate or "upload.docx"


def create_app(*, workdir: Path | None = None) -> FastAPI:
    """Build the GUI FastAPI app.

    Args:
        workdir: parent directory each upload's per-session working
            directory (``workdir/<uuid4>/``) is created under. Defaults to a
            fresh ``tempfile.mkdtemp`` when not given -- pass a fixed
            directory (e.g. the CLI's ``--workdir``) to keep converted
            output around across server restarts.
    """
    root = (
        Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="latextify-gui-"))
    )
    root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="LaTeXtify", docs_url=None, redoc_url=None)
    app.state.workdir = root
    # Opaque server-issued token -> real compiled PDF path. Populated only by
    # a successful --pdf compile in /api/convert; /api/pdf/{token} only ever
    # reads from this dict, never from the URL path itself (see module
    # docstring's Security section).
    app.state.pdf_tokens: dict[str, Path] = {}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_INDEX_HTML)

    @app.get("/api/journals", response_model=list[JournalInfo])
    def list_journals() -> list[JournalInfo]:
        infos: list[JournalInfo] = []
        for name in templates_loader.available():
            try:
                journal = templates_loader.load(name)
            except ManifestError:
                # A broken manifest shouldn't take down the whole listing --
                # skip it silently the way a directory-scan-based discover()
                # already tolerates non-journal subdirectories.
                continue
            infos.append(JournalInfo(name=name, modes=sorted(journal.bib_modes)))
        return infos

    @app.post("/api/convert", response_model=ConvertResponse)
    async def convert(
        file: UploadFile = File(...),
        journal: str = Form(...),
        citation_style: str | None = Form(None),
        pdf: bool = Form(False),
    ) -> ConvertResponse:
        try:
            journal_obj = templates_loader.load(journal)
        except ManifestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        docx_path = upload_dir / _safe_filename(file.filename)
        docx_path.write_bytes(await file.read())

        try:
            result = emit_project(
                docx_path,
                journal,
                session_dir / "output",
                citation_style=citation_style,
            )
        except ValueError as exc:
            # Every ingest-boundary module raises a clean ValueError naming
            # the problem for a corrupt/unsupported .docx or an unsupported
            # citation style (ManifestError is itself a ValueError subclass)
            # -- see latextify.cli's `convert` command for the identical
            # contract. Never let one surface as a raw 500 traceback.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        warnings = [w.message for w in result.warnings]
        pdf_url: str | None = None
        success = True

        if pdf:
            try:
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                compile_result = compile_document(
                    result.main_tex_path,
                    tectonic_path=ensure_tectonic(),
                    vendor_dir=vendor_dir,
                )
            except Exception as exc:
                # Mirrors the CLI's `except Exception` around --pdf: a hung
                # compile raises subprocess.TimeoutExpired, a present-but-
                # broken tectonic binary raises OSError. Never a raw 500
                # traceback for either.
                raise HTTPException(
                    status_code=500, detail=f"compilation failed: {exc}"
                ) from exc

            success = compile_result.success
            if result.report_path is not None:
                write_report(
                    result.report_path,
                    preflight=None,
                    emit_result=result,
                    reconciliation=None,
                    compile_result=compile_result,
                )
            if compile_result.success and compile_result.pdf_path is not None:
                token = uuid.uuid4().hex
                app.state.pdf_tokens[token] = compile_result.pdf_path
                pdf_url = f"/api/pdf/{token}"

        report_md = ""
        if result.report_path is not None and result.report_path.is_file():
            report_md = result.report_path.read_text(encoding="utf-8")

        return ConvertResponse(
            output_dir=str(result.output_dir),
            warnings=warnings,
            report_md=report_md,
            success=success,
            pdf_url=pdf_url,
        )

    @app.get("/api/pdf/{token}", include_in_schema=False)
    def get_pdf(token: str) -> FileResponse:
        pdf_path = app.state.pdf_tokens.get(token)
        if pdf_path is None or not pdf_path.is_file():
            raise HTTPException(status_code=404, detail="unknown or expired PDF token")
        return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)

    return app
