"""The single place a file format is registered for inspection/sanitizing.

Per the project's dual-registration rule, a format is declared **here and
nowhere else**: the CLI's accept list, the GUI's upload allowlist and the
tests all derive their supported-format sets from :func:`supported_extensions`
rather than restating them. Restating is how a format ends up half-wired --
accepted by the upload form, rejected by the engine.

Each handler module exposes the same two functions::

    inspect(path)        -> (findings, warnings)
    sanitize(src, dest)  -> (removed_findings, warnings)

so adding a format is one import and one row in :data:`_HANDLERS`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from . import docx_adapter, images, ole, pdf, pptx, xlsx
from .report import Finding, InspectReport, SanitizeReport


class _Handler(Protocol):
    def inspect(self, path: Path) -> tuple[list[Finding], list[str]]: ...

    def sanitize(
        self, src: Path, dest: Path, **options: object
    ) -> tuple[list[Finding], list[str]]: ...


#: extension -> (handler module, human format name)
_HANDLERS: dict[str, tuple[object, str]] = {
    ".docx": (docx_adapter, "Word document"),
    ".pptx": (pptx, "PowerPoint presentation"),
    ".xlsx": (xlsx, "Excel workbook"),
    ".pdf": (pdf, "PDF"),
    ".doc": (ole, "legacy Word document"),
    ".ppt": (ole, "legacy PowerPoint presentation"),
    ".xls": (ole, "legacy Excel workbook"),
}
_HANDLERS.update({ext: (images, "image") for ext in images.IMAGE_EXTENSIONS})


def supported_extensions() -> tuple[str, ...]:
    """Every extension the privacy engine handles, sorted."""
    return tuple(sorted(_HANDLERS))


def format_name(path: Path | str) -> str:
    """Human-readable format name for ``path``'s extension."""
    return _lookup(Path(path))[1]


def is_supported(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _HANDLERS


def _lookup(path: Path) -> tuple[_Handler, str]:
    ext = path.suffix.lower()
    entry = _HANDLERS.get(ext)
    if entry is None:
        supported = ", ".join(supported_extensions())
        raise ValueError(
            f"{path}: unsupported file type {ext or '(no extension)'!r}. Supported: {supported}"
        )
    handler, name = entry
    return handler, name  # type: ignore[return-value]


def inspect_file(path: Path | str) -> InspectReport:
    """Report what metadata and hidden content ``path`` carries.

    Never writes anything. This is the honest path for formats that cannot be
    safely rewritten, and the way to verify a sanitized file really is clean.
    """
    path = Path(path)
    handler, name = _lookup(path)
    if not path.is_file():
        raise ValueError(f"{path}: no such file")

    findings, warnings = handler.inspect(path)
    return InspectReport(
        path=str(path), file_format=name, findings=list(findings), warnings=list(warnings)
    )


def sanitize_file(src: Path | str, dest: Path | str, **options: object) -> SanitizeReport:
    """Write a metadata-stripped copy of ``src`` to ``dest``.

    ``options`` are forwarded to the handler; unknown keys are ignored, so a
    caller can pass a uniform option set across formats (e.g. ``keep_notes``,
    which only PowerPoint acts on).
    """
    src, dest = Path(src), Path(dest)
    handler, name = _lookup(src)
    if not src.is_file():
        raise ValueError(f"{src}: no such file")
    if src.resolve() == dest.resolve():
        raise ValueError(
            f"{src}: refusing to sanitize a file onto itself; choose a different "
            "destination so the original is preserved"
        )

    removed, warnings = handler.sanitize(src, dest, **options)
    return SanitizeReport(
        path=str(src),
        dest=str(dest),
        file_format=name,
        removed=list(removed),
        warnings=list(warnings),
    )
