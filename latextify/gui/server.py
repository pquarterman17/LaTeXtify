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
    POST /api/convert         single-docx multipart upload -> JSON result
    POST /api/convert-multi   main + supplement + figures + .bib + options
    GET  /api/pdf/{token}     stream a compiled PDF (server-issued token only)
    GET  /api/zip/{token}     stream a project .zip (server-issued token only)
    POST /api/pick-folder     open a native folder dialog on the server host
    POST /api/export          copy a previewed conversion's artifacts to a folder

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

import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latextify.audit.equations import write_equation_audit
from latextify.compile.pdf import staple_pdfs
from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader
from latextify.templates.loader import ManifestError

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

# Upload streaming: never hold a whole payload in RAM. Starlette already spools
# a large upload to a temp file, so the memory spike comes only from a single
# ``.read()`` of the whole thing -- copying in 1 MiB chunks is the fix. The cap
# is generous: real manuscripts embed multi-MB figures, and a figure file
# dropped separately can be large too.
_UPLOAD_CHUNK = 1 << 20  # 1 MiB
_MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB per file


class JournalInfo(BaseModel):
    """One entry of ``GET /api/journals``."""

    name: str
    display_name: str
    modes: list[str]


class ConvertResponse(BaseModel):
    """Body of ``POST /api/convert``."""

    output_dir: str
    warnings: list[str]
    report_md: str
    success: bool
    pdf_url: str | None = None


class ConvertMultiResponse(BaseModel):
    """Body of ``POST /api/convert-multi`` (the multi-file intake).

    Every ``*_url`` is a server-issued download token or ``None`` when that
    artifact was not produced (no supplement, combine off, audit off, zip off,
    or a compile that failed). ``success`` is the main document's compile
    outcome (``True`` when ``--pdf`` was off and emission succeeded).
    """

    output_dir: str
    warnings: list[str]
    report_md: str
    success: bool
    pdf_url: str | None = None
    supplement_pdf_url: str | None = None
    combined_pdf_url: str | None = None
    audit_pdf_url: str | None = None
    zip_url: str | None = None
    #: Folder the selected artifacts were copied to (when an inline export was requested).
    exported_to: str | None = None
    #: Names of the artifacts copied to ``exported_to``.
    exported: list[str] = []
    #: Opaque handle to this conversion's produced artifacts, for a later
    #: ``POST /api/export`` (the preview-then-export flow). None only if the
    #: session store is somehow unavailable.
    export_token: str | None = None


class PickFolderResponse(BaseModel):
    """Body of ``POST /api/pick-folder``. ``path`` is "" when cancelled/unavailable."""

    path: str


class ExportRequest(BaseModel):
    """Body of ``POST /api/export`` -- copy a prior conversion's artifacts out.

    ``export_token`` is the handle returned by ``/api/convert-multi``; it maps,
    server side only, to that run's produced artifacts. This lets the UI preview
    a conversion first and export the *same* result afterwards without
    recompiling.
    """

    export_token: str
    export_dir: str
    export_types: list[str] = []


class ExportResponse(BaseModel):
    """Body of ``POST /api/export``."""

    exported_to: str
    exported: list[str]
    warnings: list[str] = []


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


def _issue_token(tokens: dict[str, Path], path: Path) -> str:
    """Map a fresh opaque token to ``path`` and return it (never the path itself)."""
    token = uuid.uuid4().hex
    tokens[token] = path
    return token


# Artifact types the Export panel can copy to a chosen folder. Keys are the
# values the frontend sends; each maps to a produced path (or the project tree).
_EXPORTABLE = ("project", "main_pdf", "combined_pdf", "audit_pdf", "zip")

_PICK_FOLDER_SCRIPT = (
    "import tkinter, tkinter.filedialog as fd, sys\n"
    "r = tkinter.Tk(); r.withdraw()\n"
    "try: r.attributes('-topmost', True)\n"
    "except Exception: pass\n"
    "p = fd.askdirectory(title='Choose an export folder') or ''\n"
    "r.destroy()\n"
    "sys.stdout.write(p)\n"
)


def _pick_folder_native(timeout: float = 300.0) -> str:
    """Open a native folder picker on the server host; return the chosen path or "".

    Runs tkinter's ``askdirectory`` in a SEPARATE process: a GUI dialog cannot
    run on uvicorn's worker thread on every platform (macOS requires the main
    thread), and a subprocess also contains a crash/hang. Returns "" when the
    user cancels or no GUI/tkinter is available (a headless server), so the
    caller falls back to manual path entry -- never raises.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PICK_FOLDER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _export_artifacts(
    export_dir: str, types: set[str], *, output_dir: Path, produced: dict[str, Path]
) -> tuple[str, list[str], list[str]]:
    """Copy the selected artifact ``types`` into ``export_dir`` (created if needed).

    Returns ``(destination, exported, warnings)``. A requested type that was not
    produced (e.g. ``combined_pdf`` without combine) is reported as a warning
    rather than failing the whole export. ``project`` copies the whole output
    tree; ``zip`` copies the produced archive or builds one on demand.
    """
    dest = Path(export_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    warnings: list[str] = []
    for kind in _EXPORTABLE:
        if kind not in types:
            continue
        if kind == "project":
            target = dest / output_dir.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(output_dir, target)
            exported.append(f"project ({output_dir.name}/)")
        elif kind == "zip":
            zip_dest = dest / "latextify-project.zip"
            if "zip" in produced:
                shutil.copy2(produced["zip"], zip_dest)
            else:
                shutil.make_archive(str(zip_dest.with_suffix("")), "zip", root_dir=output_dir)
            exported.append("latextify-project.zip")
        elif kind in produced:
            shutil.copy2(produced[kind], dest / produced[kind].name)
            exported.append(produced[kind].name)
        else:
            warnings.append(
                f"export: '{kind}' was requested but not produced -- enable the "
                "matching option (Compile PDF / Combine supplement / Equation-audit)."
            )
    return str(dest), exported, warnings


async def _stream_upload(
    upload: UploadFile, dest: Path, *, max_bytes: int = _MAX_UPLOAD_BYTES
) -> None:
    """Copy ``upload`` to ``dest`` in chunks, never buffering the whole payload.

    Enforces a generous per-file size cap: a payload past the cap raises HTTP
    413 (and removes the partial file) rather than filling the disk. ``dest``'s
    parent must already exist.
    """
    total = 0
    with dest.open("wb") as out:
        while chunk := await upload.read(_UPLOAD_CHUNK):
            total += len(chunk)
            if total > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{upload.filename or 'upload'} exceeds the "
                        f"{max_bytes // (1024 * 1024)} MB per-file limit"
                    ),
                )
            out.write(chunk)


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
    # Opaque token -> project .zip path (same pattern as pdf_tokens; served by
    # GET /api/zip/{token}). Populated only by a convert-multi run with
    # want_zip=True.
    app.state.zip_tokens: dict[str, Path] = {}
    # Opaque token -> {"output_dir": Path, "produced": dict[str, Path]} for a
    # completed convert-multi run, so POST /api/export can copy that exact
    # result's artifacts out later (the preview-then-export flow) without
    # recompiling. Same lifetime/growth characteristics as the token dicts above.
    app.state.export_sessions: dict[str, dict[str, object]] = {}

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
            infos.append(
                JournalInfo(
                    name=name,
                    display_name=journal.display_name,
                    modes=sorted(journal.bib_modes),
                )
            )
        # Alphabetical by the label the user actually reads.
        infos.sort(key=lambda info: info.display_name.lower())
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
        await _stream_upload(file, docx_path)

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

    @app.post("/api/convert-multi", response_model=ConvertMultiResponse)
    async def convert_multi(
        main: UploadFile = File(...),
        journal: str = Form(...),
        supplement: UploadFile | None = File(None),
        figures: list[UploadFile] = File([]),
        figure_numbers: list[int] = Form([]),
        references: UploadFile | None = File(None),
        citation_style: str | None = Form(None),
        crossref_mailto: str | None = Form(None),
        combine: bool = Form(False),
        supplement_onecolumn: bool = Form(False),
        equation_audit: bool = Form(False),
        check_references: bool = Form(False),
        want_zip: bool = Form(False),
        pdf: bool = Form(True),
        export_dir: str | None = Form(None),
        export_types: list[str] = Form([]),
    ) -> ConvertMultiResponse:
        """Convert a main manuscript plus optional supplement/figures/.bib in one call.

        Figures are dropped in as ``figures/fig<N>.<ext>`` beside the main docx
        (folder-convention override); the ``.bib`` seeds offline citation
        matching; ``combine`` staples main+supplement into ``combined.pdf``;
        ``equation_audit`` emits a numbered ``audit.pdf``; ``want_zip`` packages
        the whole project tree. Every produced artifact is returned as an opaque
        download token.
        """
        # combine needs both a supplement and a compile step (mirror the CLI).
        if combine and supplement is None:
            raise HTTPException(status_code=400, detail="combine requires a supplement file")
        if combine and not pdf:
            raise HTTPException(status_code=400, detail="combine requires pdf compilation")
        if len(figures) != len(figure_numbers):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"figures ({len(figures)}) and figure_numbers "
                    f"({len(figure_numbers)}) must have the same length"
                ),
            )

        try:
            journal_obj = templates_loader.load(journal)
        except ManifestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)

        main_path = upload_dir / _safe_filename(main.filename)
        await _stream_upload(main, main_path)

        supplement_path: Path | None = None
        if supplement is not None:
            supplement_path = upload_dir / ("supplement_" + _safe_filename(supplement.filename))
            await _stream_upload(supplement, supplement_path)

        references_path: Path | None = None
        if references is not None:
            references_path = upload_dir / _safe_filename(references.filename)
            await _stream_upload(references, references_path)

        # Figure files land as figures/fig<N>.<ext> beside the main docx so the
        # existing folder-convention override picks them up. NB overrides REPLACE
        # an embedded figure -- a docx with no embedded image for figure N has
        # nothing to attach the dropped file to (multi-file plan, Context).
        if figures:
            figures_override_dir = upload_dir / "figures"
            figures_override_dir.mkdir(exist_ok=True)
            for fig_upload, number in zip(figures, figure_numbers, strict=True):
                ext = Path(fig_upload.filename or "").suffix.lstrip(".").lower() or "png"
                await _stream_upload(fig_upload, figures_override_dir / f"fig{number}.{ext}")

        try:
            result = emit_project(
                main_path,
                journal,
                session_dir / "output",
                citation_style=citation_style,
                crossref_mailto=crossref_mailto,
                supplement_docx_path=supplement_path,
                references_bib_path=references_path,
                supplement_onecolumn=supplement_onecolumn,
                check_references=check_references,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        warnings = [w.message for w in result.warnings]
        if result.supplement is not None:
            warnings.extend(w.message for w in result.supplement.warnings)

        pdf_url: str | None = None
        supplement_pdf_url: str | None = None
        combined_pdf_url: str | None = None
        audit_pdf_url: str | None = None
        zip_url: str | None = None
        success = True
        # Real paths of every produced artifact, for the optional folder export.
        produced: dict[str, Path] = {"project": result.output_dir}

        pdf_tokens = app.state.pdf_tokens
        if pdf:
            try:
                tectonic = ensure_tectonic()
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                main_compile = compile_document(
                    result.main_tex_path, tectonic_path=tectonic, vendor_dir=vendor_dir
                )
                success = main_compile.success
                if main_compile.success and main_compile.pdf_path is not None:
                    pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, main_compile.pdf_path)}"
                    produced["main_pdf"] = main_compile.pdf_path

                supplement_compile = None
                if result.supplement is not None:
                    supplement_compile = compile_document(
                        result.supplement.supplement_tex_path,
                        tectonic_path=tectonic,
                        vendor_dir=vendor_dir,
                    )
                    if supplement_compile.success and supplement_compile.pdf_path is not None:
                        supplement_pdf_url = (
                            f"/api/pdf/{_issue_token(pdf_tokens, supplement_compile.pdf_path)}"
                        )

                if (
                    combine
                    and main_compile.success
                    and supplement_compile is not None
                    and supplement_compile.success
                ):
                    combined = result.output_dir / "combined.pdf"
                    staple_pdfs([main_compile.pdf_path, supplement_compile.pdf_path], combined)
                    combined_pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, combined)}"
                    produced["combined_pdf"] = combined

                if result.report_path is not None:
                    write_report(
                        result.report_path,
                        preflight=None,
                        emit_result=result,
                        reconciliation=None,
                        compile_result=main_compile,
                        supplement=result.supplement,
                    )
            except HTTPException:
                raise
            except Exception as exc:
                # Mirrors /api/convert: a hung/broken compile is a 500, not a raw
                # traceback. The LaTeX project itself is still written to disk.
                raise HTTPException(
                    status_code=500, detail=f"compilation failed: {exc}"
                ) from exc

        if equation_audit:
            try:
                audit = write_equation_audit(
                    main_path,
                    session_dir / "audit",
                    compile_pdf=pdf,
                    tectonic_path=ensure_tectonic() if pdf else None,
                )
                if audit.audit_pdf_path is not None and audit.audit_pdf_path.is_file():
                    audit_pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, audit.audit_pdf_path)}"
                    produced["audit_pdf"] = audit.audit_pdf_path
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"equation audit failed: {exc}"
                ) from exc

        if want_zip:
            archive = shutil.make_archive(
                str(session_dir / "project"), "zip", root_dir=result.output_dir
            )
            produced["zip"] = Path(archive)
            zip_url = f"/api/zip/{_issue_token(app.state.zip_tokens, Path(archive))}"

        # Optional export: copy the selected artifact types to a chosen folder on
        # the user's machine (this is a localhost tool; the folder came from the
        # native picker or manual entry). Never fatal to a successful conversion.
        exported_to: str | None = None
        exported: list[str] = []
        if export_dir and export_dir.strip():
            try:
                exported_to, exported, export_warnings = _export_artifacts(
                    export_dir.strip(),
                    set(export_types),
                    output_dir=result.output_dir,
                    produced=produced,
                )
                warnings.extend(export_warnings)
            except OSError as exc:
                raise HTTPException(
                    status_code=400, detail=f"could not export to {export_dir!r}: {exc}"
                ) from exc

        # Register this run's artifacts so the UI can export them later without
        # recompiling (preview-then-export). The inline export above stays for
        # one-shot/programmatic callers.
        export_token = uuid.uuid4().hex
        app.state.export_sessions[export_token] = {
            "output_dir": result.output_dir,
            "produced": produced,
        }

        report_md = ""
        if result.report_path is not None and result.report_path.is_file():
            report_md = result.report_path.read_text(encoding="utf-8")

        return ConvertMultiResponse(
            output_dir=str(result.output_dir),
            warnings=warnings,
            report_md=report_md,
            success=success,
            pdf_url=pdf_url,
            supplement_pdf_url=supplement_pdf_url,
            combined_pdf_url=combined_pdf_url,
            audit_pdf_url=audit_pdf_url,
            zip_url=zip_url,
            exported_to=exported_to,
            exported=exported,
            export_token=export_token,
        )

    @app.get("/api/pdf/{token}", include_in_schema=False)
    def get_pdf(token: str) -> FileResponse:
        pdf_path = app.state.pdf_tokens.get(token)
        if pdf_path is None or not pdf_path.is_file():
            raise HTTPException(status_code=404, detail="unknown or expired PDF token")
        return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)

    @app.get("/api/zip/{token}", include_in_schema=False)
    def get_zip(token: str) -> FileResponse:
        zip_path = app.state.zip_tokens.get(token)
        if zip_path is None or not zip_path.is_file():
            raise HTTPException(status_code=404, detail="unknown or expired zip token")
        return FileResponse(
            zip_path, media_type="application/zip", filename="latextify-project.zip"
        )

    @app.post("/api/pick-folder", response_model=PickFolderResponse)
    def pick_folder() -> PickFolderResponse:
        # Opens a native folder dialog on the machine hosting the server (the
        # user's own machine -- this is a localhost tool). Returns "" when
        # cancelled or unavailable; the UI then falls back to manual entry.
        return PickFolderResponse(path=_pick_folder_native())

    @app.post("/api/export", response_model=ExportResponse)
    def export(req: ExportRequest) -> ExportResponse:
        # Copy a previously-previewed conversion's artifacts to a chosen folder.
        # The token maps to that run's produced paths; an unknown/expired token
        # (e.g. server restarted, or inputs changed so the UI dropped it) is a
        # 404 telling the user to convert again -- never a path lookup from the
        # request.
        session = app.state.export_sessions.get(req.export_token)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="unknown or expired export token -- preview the conversion again",
            )
        if not req.export_dir.strip():
            raise HTTPException(status_code=400, detail="no destination folder given")
        try:
            dest, exported, warnings = _export_artifacts(
                req.export_dir.strip(),
                set(req.export_types),
                output_dir=session["output_dir"],  # type: ignore[arg-type]
                produced=session["produced"],  # type: ignore[arg-type]
            )
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"could not export to {req.export_dir!r}: {exc}"
            ) from exc
        return ExportResponse(exported_to=dest, exported=exported, warnings=warnings)

    return app
