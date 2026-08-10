"""Local web GUI: FastAPI app wrapping the conversion pipeline (plan item 19).

Buildless: ``static/index.html`` plus plain ``style.css`` / ``app.js`` /
``review.js`` siblings served under ``/static`` (vanilla JS, no build step,
no CDN) talk to the JSON/file endpoints. This module contains
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
    POST /api/clean-docx      sanitize an uploaded .docx -> token + CleanReport
    GET  /api/clean/{token}   stream the sanitized .docx (server-issued token only)
    POST /api/export-format   export an uploaded manuscript to HTML/Markdown -> token
    GET  /api/alt/{token}     stream the exported HTML/Markdown (server-issued token only)
    POST /api/pick-folder     open a native folder dialog on the server host
    POST /api/export          copy a previewed conversion's artifacts to a folder
    POST /api/heartbeat       tab-alive ping used by the local auto-shutdown launcher
    POST /api/tab-closed      tab-closed beacon used by the local auto-shutdown launcher

The two single-upload processing routes (``/api/clean-docx``,
``/api/export-format``) are registered by :mod:`latextify.gui.uploads_routes`
rather than defined here; the token-gated GET downloads live in
:mod:`latextify.gui.downloads`; the heartbeat/tab-closed routes above live in
:mod:`latextify.gui.lifecycle`; and the reference-review JSON shaping lives in
:mod:`latextify.gui.validation_view` -- all moved out to keep this module
under its size-ratchet pin.

Security
--------
This module never chooses the bind address -- see :func:`latextify.cli.gui`,
which binds ``127.0.0.1`` only (uploaded manuscripts are private; this is a
local tool, not a hosted service). The one sanctioned hosted deployment is the
public *demo* (``create_app(demo=True)``, run by ``python -m
latextify.gui.demo``), which trades the loopback assumptions for the explicit
hardening in :mod:`latextify.gui.demo`.

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

import re
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from latextify.citations.bib import entries_to_bib
from latextify.citations.corrections import apply_corrections, entry_from_dict
from latextify.compile.pdf import staple_pdfs
from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.gui.convert_routes import register_convert_routes
from latextify.gui.demo import (
    DEMO_MAX_UPLOAD_BYTES,
    RateLimiter,
    inject_demo_banner,
    require_demo_rate_limit,
)
from latextify.gui.downloads import (
    _issue_token,
    _rmtree,
    _touch_session,
    register_download_routes,
)
from latextify.gui.exporting import _export_artifacts
from latextify.gui.folder_picker import pick_folder_native
from latextify.gui.guard import inject_gui_secret, new_gui_secret, require_gui_auth
from latextify.gui.lifecycle import register_lifecycle, start_client_monitor
from latextify.gui.schemas import (
    ApplyCorrectionsRequest,
    ApplyCorrectionsResponse,
    ExportRequest,
    ExportResponse,
    JournalInfo,
    PickFolderResponse,
)
from latextify.gui.upload_utils import (
    _MAX_UPLOAD_BYTES,
)
from latextify.gui.uploads_routes import register_upload_routes
from latextify.model.refs import RefEntry
from latextify.model.validate import CorrectionDecision, ValidationReport
from latextify.templates import loader as templates_loader
from latextify.templates.loader import ManifestError

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

#: 403 detail for the server-filesystem endpoints when demo mode disables them.
_DEMO_FS_DISABLED = (
    "folder export is disabled in the hosted demo -- download the PDF or the project .zip instead"
)

#: Matches a served /static/*.css or /static/*.js reference in index.html so
#: index() can append a per-process cache-busting query token to it (see
#: create_app's cache_bust state and its docstring).
_STATIC_ASSET_RE = re.compile(r'(/static/[\w.-]+\.(?:css|js))"')


def create_app(
    *,
    workdir: Path | None = None,
    gui_secret: str | None = None,
    demo: bool = False,
    auto_shutdown: bool = False,
) -> FastAPI:
    """Build the GUI FastAPI app.

    Args:
        workdir: parent directory each upload's per-session working
            directory (``workdir/<uuid4>/``) is created under. Defaults to a
            fresh ``tempfile.mkdtemp`` when not given -- pass a fixed
            directory (e.g. the CLI's ``--workdir``) to keep converted
            output around across server restarts.
        gui_secret: the per-process secret mutating ``/api/*`` requests must
            carry (see :mod:`latextify.gui.guard`). Defaults to a fresh random
            token; tests inject a deterministic value without weakening the
            production default.
        demo: hosted-demo hardening (see :mod:`latextify.gui.demo`): disables
            the server-filesystem export endpoints, lowers the upload cap,
            rate-limits conversions per client, and injects a privacy banner.
            The default (off) is the unchanged local tool.
        auto_shutdown: start the background monitor (see
            :mod:`latextify.gui.lifecycle`) that stops the server once every
            browser tab showing this page has closed. Off by default; the
            local ``gui`` CLI command turns it on unless ``--keep-alive`` is
            given, and the hosted demo never turns it on.
    """
    root = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="latextify-gui-"))
    root.mkdir(parents=True, exist_ok=True)
    # Only a root WE created (no caller workdir) is ours to delete on shutdown;
    # a caller-supplied --workdir is persistent and left untouched.
    owns_root = workdir is None

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # The auto-shutdown monitor is an asyncio task, not a thread, so it
        # starts/stops on this same app lifecycle rather than atexit.
        monitor_task = start_client_monitor(app) if auto_shutdown else None
        yield
        if monitor_task is not None:
            monitor_task.cancel()
        # Shutdown: drop the temp tree holding uploaded manuscripts + artifacts.
        # Wired to the app lifecycle (not atexit) so it runs on a clean stop.
        if owns_root:
            _rmtree(root)

    app = FastAPI(title="LaTeXtify", docs_url=None, redoc_url=None, lifespan=_lifespan)
    app.state.workdir = root
    app.state.owns_root = owns_root
    # Opaque server-issued token -> real compiled PDF path. Populated only by
    # a successful --pdf compile in /api/convert; /api/pdf/{token} only ever
    # reads from this dict, never from the URL path itself (see module
    # docstring's Security section).
    app.state.pdf_tokens: dict[str, Path] = {}
    # Opaque token -> project .zip path (same pattern as pdf_tokens; served by
    # GET /api/zip/{token}). Populated only by a convert-multi run with
    # want_zip=True.
    app.state.zip_tokens: dict[str, Path] = {}
    # Opaque token -> sanitized .docx path (same pattern as pdf_tokens/zip_tokens;
    # served by GET /api/clean/{token}). Populated only by a successful
    # POST /api/clean-docx run.
    app.state.clean_tokens: dict[str, Path] = {}
    # Opaque token -> exported .html/.md path (same pattern as clean_tokens;
    # served by GET /api/alt/{token}). Populated only by a successful
    # POST /api/export-format run.
    app.state.alt_tokens: dict[str, Path] = {}
    # Opaque token -> {"output_dir": Path, "produced": dict[str, Path]} for a
    # completed convert-multi run, so POST /api/export can copy that exact
    # result's artifacts out later (the preview-then-export flow) without
    # recompiling. Same lifetime/growth characteristics as the token dicts above.
    app.state.export_sessions: dict[str, dict[str, object]] = {}
    # Per-process secret required on mutating /api/* requests (audit item 4).
    # Only the served page learns it (index() injects it); a cross-origin
    # attacker page can't read it under the same-origin policy.
    app.state.gui_secret = gui_secret if gui_secret is not None else new_gui_secret()
    # Hosted-demo hardening (see latextify.gui.demo). A None limiter makes the
    # rate-limit dependency a no-op, so the local tool is untouched.
    app.state.demo_mode = demo
    app.state.rate_limiter = RateLimiter() if demo else None
    max_upload_bytes = DEMO_MAX_UPLOAD_BYTES if demo else _MAX_UPLOAD_BYTES
    # A fresh token per process start: appended to every served /static/*.css
    # or /static/*.js reference (see index() below) so a browser that cached
    # a prior run's assets always fetches this run's files instead of a
    # stale, heuristically-cached copy of an earlier redesign.
    app.state.cache_bust = uuid.uuid4().hex[:8]

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # Serve the static page with the per-process secret injected so the
        # page's own fetches carry it; the raw file on disk never contains it.
        html = _INDEX_HTML.read_text(encoding="utf-8")
        if demo:
            html = inject_demo_banner(html)
        html = _STATIC_ASSET_RE.sub(rf'\1?v={app.state.cache_bust}"', html)
        response = HTMLResponse(inject_gui_secret(html, app.state.gui_secret))
        response.headers["Cache-Control"] = "no-cache"
        return response

    # The page's stylesheet + scripts (no secret material lives in them; the
    # secret wrapper is injected only into the served index above).
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

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
                    default_mode=journal.default_mode,
                )
            )
        # Alphabetical by the label the user actually reads.
        infos.sort(key=lambda info: info.display_name.lower())
        return infos

    register_convert_routes(app, root=root, max_upload_bytes=max_upload_bytes, demo=demo)

    # Single-manuscript upload-processing routes (/api/clean-docx,
    # /api/export-format) live in latextify.gui.uploads_routes, and their
    # token-gated GET downloads (/api/pdf, /api/zip, /api/clean, /api/alt) in
    # latextify.gui.downloads -- both extracted to keep this module under its
    # size-ratchet pin.
    register_upload_routes(app, root=root, max_upload_bytes=max_upload_bytes)
    register_download_routes(app)
    # Tab-heartbeat routes (always registered; only start_client_monitor
    # above, gated on auto_shutdown, ever acts on them).
    register_lifecycle(app)

    @app.post(
        "/api/pick-folder",
        response_model=PickFolderResponse,
        dependencies=[Depends(require_gui_auth)],
    )
    def pick_folder() -> PickFolderResponse:
        # Opens a native folder dialog on the machine hosting the server (the
        # user's own machine -- this is a localhost tool). Returns "" when
        # cancelled or unavailable; the UI then falls back to manual entry.
        if demo:  # a dialog on a shared host is meaningless; the UI hides this
            raise HTTPException(status_code=403, detail=_DEMO_FS_DISABLED)
        return PickFolderResponse(path=pick_folder_native())

    @app.post(
        "/api/export",
        response_model=ExportResponse,
        dependencies=[Depends(require_gui_auth)],
    )
    def export(req: ExportRequest) -> ExportResponse:
        # Copy a previously-previewed conversion's artifacts to a chosen folder.
        # The token maps to that run's produced paths; an unknown/expired token
        # (e.g. server restarted, or inputs changed so the UI dropped it) is a
        # 404 telling the user to convert again -- never a path lookup from the
        # request.
        if demo:  # never write to a caller-chosen path on a shared host
            raise HTTPException(status_code=403, detail=_DEMO_FS_DISABLED)
        session = app.state.export_sessions.get(req.export_token)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="unknown or expired export token -- preview the conversion again",
            )
        _touch_session(session)  # active export defers this session's expiry
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

    @app.post(
        "/api/apply-corrections",
        response_model=ApplyCorrectionsResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    def apply_corrections_endpoint(req: ApplyCorrectionsRequest) -> ApplyCorrectionsResponse:
        """Apply reviewed reference corrections to a prior run and recompile.

        Rewrites the session's ``references.bib`` with the author's accepted
        approve/deny/edit decisions, then -- if that run compiled a PDF --
        rebuilds the PDF (and supplement/combined) so the download reflects the
        fixes. Idempotent-friendly: the session's entry set is updated in place,
        so a second apply builds on the corrected bibliography.
        """
        session = app.state.export_sessions.get(req.export_token)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="unknown or expired token -- preview the conversion again",
            )
        _touch_session(session)  # applying corrections is active use; defer expiry
        report = session.get("validation")
        if not isinstance(report, ValidationReport):
            raise HTTPException(
                status_code=400,
                detail="this conversion has no reference check to correct",
            )

        entries: list[RefEntry] = list(session["entries"])  # type: ignore[arg-type]
        entries_by_key = {e.key: e for e in entries}
        decisions: list[CorrectionDecision] = []
        for item in req.decisions:
            if item.action == "edit":
                base = entries_by_key.get(item.key)
                if base is None:
                    continue
                decisions.append(
                    CorrectionDecision(
                        key=item.key,
                        action="edit",
                        edited_entry=entry_from_dict(item.entry or {}, base=base),
                    )
                )
            else:
                decisions.append(CorrectionDecision(key=item.key, action=item.action))

        applied = sum(1 for d in decisions if d.action in ("approve", "edit"))
        corrected = apply_corrections(entries, report, decisions)
        session["bib_path"].write_text(  # type: ignore[union-attr]
            entries_to_bib(corrected), encoding="utf-8"
        )
        session["entries"] = tuple(corrected)  # subsequent applies build on this

        if applied:
            # The project .zip snapshot built at convert time now predates these
            # corrections (stale references.bib + PDFs). Drop it so /api/export
            # rebuilds a fresh archive from the corrected output_dir on demand
            # instead of exporting the pre-correction copy (tech-debt finding 2).
            session_produced = session.get("produced")
            if isinstance(session_produced, dict):
                session_produced.pop("zip", None)

        pdf_url: str | None = None
        supplement_pdf_url: str | None = None
        combined_pdf_url: str | None = None
        success = True
        warnings: list[str] = []
        if applied and session.get("compiled"):
            try:
                journal_obj = templates_loader.load(session["journal"])  # type: ignore[arg-type]
                tectonic = ensure_tectonic()
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                produced: dict[str, Path] = session["produced"]  # type: ignore[assignment]

                main_compile = compile_document(
                    session["main_tex_path"], tectonic_path=tectonic, vendor_dir=vendor_dir
                )
                if main_compile.success and main_compile.pdf_path is not None:
                    token = _issue_token(app.state.pdf_tokens, main_compile.pdf_path)
                    pdf_url = f"/api/pdf/{token}"
                    produced["main_pdf"] = main_compile.pdf_path

                supplement_compile = None
                if session.get("supplement_tex_path"):
                    supplement_compile = compile_document(
                        session["supplement_tex_path"],
                        tectonic_path=tectonic,
                        vendor_dir=vendor_dir,
                    )
                    if supplement_compile.success and supplement_compile.pdf_path is not None:
                        token = _issue_token(app.state.pdf_tokens, supplement_compile.pdf_path)
                        supplement_pdf_url = f"/api/pdf/{token}"
                        produced["supplement_pdf"] = supplement_compile.pdf_path
                    else:
                        warnings.append("supplement PDF failed to recompile (main is unaffected).")

                # Same honest-success rule as convert-multi: every recompiled
                # document must succeed for the overall result to be a success.
                success = main_compile.success and (
                    supplement_compile is None or supplement_compile.success
                )

                if (
                    session.get("combine")
                    and main_compile.success
                    and supplement_compile is not None
                    and supplement_compile.success
                ):
                    combined = session["output_dir"] / "combined.pdf"  # type: ignore[operator]
                    staple_pdfs([main_compile.pdf_path, supplement_compile.pdf_path], combined)
                    combined_pdf_url = f"/api/pdf/{_issue_token(app.state.pdf_tokens, combined)}"
                    produced["combined_pdf"] = combined
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"recompilation after corrections failed: {exc}"
                ) from exc

        return ApplyCorrectionsResponse(
            applied=applied,
            success=success,
            pdf_url=pdf_url,
            supplement_pdf_url=supplement_pdf_url,
            combined_pdf_url=combined_pdf_url,
            warnings=warnings,
        )

    return app
