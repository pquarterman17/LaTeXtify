"""Session/token lifecycle + artifact-download routes (extracted from ``server.py``).

A previewed conversion (or, since plan item 3's docx sanitizer, a clean-docx
run) writes its uploads and generated artifacts under a per-run session
directory and issues in-memory download tokens (PDF/zip/clean-docx). Without
bounds these retain private manuscripts and grow on disk/in memory forever,
so every session carries a TTL and the whole store is LRU-capped.

Two things live here, both moved out of ``server.py`` to keep that module
under its size-ratchet pin:

- The session/token bookkeeping (``_rmtree``, ``_register_session``,
  ``_touch_session``, ``_prune_sessions``, ``_issue_token``,
  ``_prune_dead_tokens``). ``server.py`` imports the ones its own route
  handlers call directly, and re-exports the TTL/cap constants so existing
  ``latextify.gui.server._prune_sessions`` / ``._SESSION_TTL_SECONDS`` /
  ``._MAX_SESSIONS`` references (including the test suite) keep working
  unchanged.
- :func:`register_download_routes`, which attaches the token-gated GET
  download endpoints (``/api/pdf/{token}``, ``/api/zip/{token}``,
  ``/api/clean/{token}``) to an app. Each does a dict lookup by an opaque
  server-issued token, never a filesystem path built from the URL -- see
  ``server.py``'s module docstring Security section for the full rationale.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Session lifecycle (audit item 3). Previews write uploads + generated artifacts
# under a per-run session directory and issue in-memory download tokens; without
# bounds these retain private manuscripts and grow on disk/in memory forever.
_SESSION_TTL_SECONDS = 3600.0  # a previewed conversion stays exportable for 1 hour
_MAX_SESSIONS = 32  # cap concurrent retained sessions; LRU-evict oldest beyond it


def _rmtree(path: Path | None) -> None:
    """Best-effort recursive delete; never raises (cleanup must not mask errors)."""
    if isinstance(path, Path):
        shutil.rmtree(path, ignore_errors=True)


def _issue_token(tokens: dict[str, Path], path: Path) -> str:
    """Map a fresh opaque token to ``path`` and return it (never the path itself)."""
    token = uuid.uuid4().hex
    tokens[token] = path
    return token


def _prune_dead_tokens(app: FastAPI) -> None:
    """Drop PDF/zip/clean tokens whose backing file is gone (its session was cleaned)."""
    for store in (app.state.pdf_tokens, app.state.zip_tokens, app.state.clean_tokens):
        for token, path in list(store.items()):
            if not path.is_file():
                store.pop(token, None)


def _prune_sessions(app: FastAPI, *, now: float) -> None:
    """Remove sessions past their TTL, deleting each one's on-disk directory."""
    sessions = app.state.export_sessions
    for token, session in list(sessions.items()):
        if now - float(session.get("_last_access", now)) > _SESSION_TTL_SECONDS:
            _rmtree(session.get("_session_dir"))  # type: ignore[arg-type]
            sessions.pop(token, None)
    _prune_dead_tokens(app)


def _touch_session(session: dict[str, object], *, now: float | None = None) -> None:
    """Refresh a session's last-access time so active use defers its expiry."""
    session["_last_access"] = now if now is not None else time.time()


def _register_session(
    app: FastAPI, token: str, session: dict[str, object], *, session_dir: Path, now: float
) -> None:
    """Register a completed run's session, pruning expired + capping total count."""
    sessions = app.state.export_sessions
    _prune_sessions(app, now=now)
    # LRU-evict (by last access) down to the cap before admitting the new one.
    while len(sessions) >= _MAX_SESSIONS:
        oldest = min(sessions, key=lambda t: float(sessions[t].get("_last_access", 0.0)))
        _rmtree(sessions[oldest].get("_session_dir"))  # type: ignore[arg-type]
        sessions.pop(oldest, None)
    session["_session_dir"] = session_dir
    session["_created"] = now
    session["_last_access"] = now
    sessions[token] = session


def register_download_routes(app: FastAPI) -> None:
    """Attach the token-gated GET download endpoints to ``app``."""

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

    @app.get("/api/clean/{token}", include_in_schema=False)
    def get_clean(token: str) -> FileResponse:
        clean_path = app.state.clean_tokens.get(token)
        if clean_path is None or not clean_path.is_file():
            raise HTTPException(status_code=404, detail="unknown or expired clean-docx token")
        return FileResponse(
            clean_path,
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            filename="cleaned.docx",
        )
