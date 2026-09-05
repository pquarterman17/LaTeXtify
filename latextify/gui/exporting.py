"""Artifact export helpers for the GUI server (extracted from ``server.py``).

Pure filesystem copying — no FastAPI imports, no app state. ``server.py``
re-exports these names, so tests and callers keep importing them from there.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

# Artifact types the Export panel can copy to a chosen folder. Keys are the
# values the frontend sends; each maps to a produced path (or the project tree).
_EXPORTABLE = ("project", "main_pdf", "supplement_pdf", "combined_pdf", "audit_pdf", "zip")

#: Extra folders (``os.pathsep``-separated) an export may target, on top of the
#: user's home directory. The GUI is a localhost tool, but the destination is
#: still a string a browser page sent, so writes are confined to known roots.
EXPORT_ROOTS_ENV = "LATEXTIFY_EXPORT_ROOTS"


def default_export_roots() -> list[Path]:
    """The folders an export may write under: home plus ``LATEXTIFY_EXPORT_ROOTS``."""
    roots = [Path.home()]
    extra = os.environ.get(EXPORT_ROOTS_ENV, "")
    roots.extend(Path(p) for p in extra.split(os.pathsep) if p.strip())
    return roots


def resolve_export_dir(export_dir: str, roots: Iterable[Path]) -> Path:
    """Normalize ``export_dir`` and prove it lies under one of ``roots``.

    Raises ``ValueError`` naming the allowed roots otherwise, so a request can
    never steer a copy to an arbitrary place on the host's filesystem.
    """
    roots = list(roots)
    # Every path is resolved (symlinks, ``..``, ``~``, and on Windows the
    # on-disk letter case of every existing component) and compared with a
    # trailing separator, so ``/home/me/papers2`` never passes as being under
    # ``/home/me/papers``, while a root itself (``dest == root``) does. No
    # ``normcase`` here: it would lowercase the path handed back to the user.
    allowed = tuple(
        os.path.realpath(os.path.expanduser(str(root))).rstrip(os.sep) + os.sep for root in roots
    )
    dest = os.path.realpath(os.path.expanduser(export_dir)) + os.sep
    if not dest.startswith(allowed):
        allowed_list = ", ".join(str(r) for r in roots) or "(none)"
        raise ValueError(
            f"export folder must be inside one of: {allowed_list} "
            f"(add more via the {EXPORT_ROOTS_ENV} environment variable)"
        )
    return Path(dest)


def _export_artifacts(
    export_dir: str,
    types: set[str],
    *,
    output_dir: Path,
    produced: dict[str, Path],
    roots: Iterable[Path] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Copy the selected artifact ``types`` into ``export_dir`` (created if needed).

    Returns ``(destination, exported, warnings)``. A requested type that was not
    produced (e.g. ``combined_pdf`` without combine) is reported as a warning
    rather than failing the whole export. ``project`` copies the whole output
    tree; ``zip`` copies the produced archive or builds one on demand.
    ``export_dir`` must resolve under one of ``roots`` (default:
    :func:`default_export_roots`); otherwise ``ValueError`` is raised before
    anything is written.
    """
    dest = resolve_export_dir(export_dir, default_export_roots() if roots is None else roots)
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
