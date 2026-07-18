"""Upload-handling helpers shared by every POST route that accepts a file
(extracted from ``server.py`` to keep that module under its size-ratchet pin).

Pure request/filesystem plumbing -- no app state, no route registration, no
conversion logic. Both :mod:`latextify.gui.server` and
:mod:`latextify.gui.uploads_routes` import directly from here (never from
each other for this), so neither creates a circular import.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile

# Upload streaming: never hold a whole payload in RAM. Starlette already spools
# a large upload to a temp file, so the memory spike comes only from a single
# ``.read()`` of the whole thing -- copying in 1 MiB chunks is the fix. The cap
# is generous: real manuscripts embed multi-MB figures, and a figure file
# dropped separately can be large too.
_UPLOAD_CHUNK = 1 << 20  # 1 MiB
_MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # 250 MB per file

# Upload validation (audit item 5). Case-insensitive extension allowlists,
# checked before anything touches disk or Pandoc. Figure extensions mirror the
# formats the conversion pipeline already handles (raster + vector + PDF);
# references accept every reference-manager export
# latextify.citations.refs_import.parse_references_file recognizes.
_ALLOWED_FIGURE_EXTS = frozenset(
    {"png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "webp", "eps", "svg", "pdf"}
)
_ALLOWED_REFERENCE_EXTS = frozenset({"bib", "ris", "json", "xml", "nbib"})
_ALLOWED_MANUSCRIPT_EXTS = frozenset({"docx", "odt", "rtf", "md"})


def _lower_ext(name: str | None) -> str:
    """Lowercase extension without the dot ("Paper.DOCX" -> "docx"); "" if none."""
    return Path(name or "").suffix.lstrip(".").lower()


def _safe_filename(name: str | None) -> str:
    """Strip any directory components from a client-supplied filename.

    ``UploadFile.filename`` is attacker-controlled. The file is always
    written under a fresh per-session directory the caller creates (never a
    path built from the filename itself), so this is defense in depth rather
    than the only guard -- but a bare basename keeps the on-disk name
    predictable and stops something like ``"../../evil.docx"`` from ever
    being interpreted as a relative path by anything downstream.
    """
    if not name:
        return "upload.docx"
    candidate = Path(name).name
    return candidate or "upload.docx"


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
