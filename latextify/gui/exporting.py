"""Artifact export helpers for the GUI server (extracted from ``server.py``).

Pure filesystem copying — no FastAPI imports, no app state. ``server.py``
re-exports these names, so tests and callers keep importing them from there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Artifact types the Export panel can copy to a chosen folder. Keys are the
# values the frontend sends; each maps to a produced path (or the project tree).
_EXPORTABLE = ("project", "main_pdf", "supplement_pdf", "combined_pdf", "audit_pdf", "zip")


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
