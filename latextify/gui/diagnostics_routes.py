"""GUI system-check endpoint for local/offline troubleshooting."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from latextify.gui.guard import require_gui_auth
from latextify.system_check import run_system_check


def register_diagnostics_routes(app: FastAPI, *, workdir: Path, demo: bool) -> None:
    @app.post("/api/system-check", dependencies=[Depends(require_gui_auth)])
    def system_check() -> dict[str, object]:
        if demo:
            raise HTTPException(
                status_code=403, detail="system check is available in the local app"
            )
        return run_system_check(workdir)
