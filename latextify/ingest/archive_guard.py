"""Resource-safety bounds for untrusted .docx (ZIP) archives.

A .docx is a ZIP archive, and several ingest steps decompress its members --
preflight parses ``word/document.xml``/``word/styles.xml`` with lxml, the
citation-sentinel pass rewrites a copy, and pandoc reads the whole package. A
small, highly compressed archive (a "zip bomb") can therefore exhaust memory or
disk *before* any of those steps notices. :func:`validate_docx_archive` is the
guard: run it at the earliest ingest boundary -- before preflight, rewriting,
XML parsing, or pandoc -- so a malicious or pathological archive is rejected up
front with a clean :class:`ValueError` naming the document and the violated
bound.

The checks read only the ZIP central directory (declared member sizes, names,
and flags); they do **not** decompress, so the guard itself is cheap and cannot
be turned into a bomb. Defence in depth: :func:`stream_zip_member` copies
members with bounded chunked reads, so even an archive whose header lies about a
member's size cannot materialise an unbounded object during a rewrite.

All bounds are module constants (documented below) and are also overridable per
call, which keeps the acceptance tests cheap -- they pass tiny limits against a
few bytes of synthetic content instead of building a real multi-gigabyte bomb.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

#: Maximum number of members. A manuscript with ~50 embedded figures has a few
#: hundred parts (media + rels + XML); a bomb ships tens of thousands.
MAX_MEMBER_COUNT = 2048

#: Maximum *decompressed* size of a single member. One high-resolution
#: TIFF/PNG figure can be tens of MB, so 256 MiB covers even an oversized
#: microscopy image with wide headroom.
MAX_MEMBER_EXPANDED_BYTES = 256 * 1024 * 1024

#: Maximum decompressed size of all members combined. Deliberately larger than
#: the GUI's 250 MB *compressed* upload cap: a legitimately figure-heavy
#: manuscript expands well past its zipped size. 1 GiB still bounds disk/RAM.
MAX_TOTAL_EXPANDED_BYTES = 1024 * 1024 * 1024

#: Maximum overall expansion ratio (total decompressed / total compressed).
#: Text XML compresses ~10-20x and images barely compress, so a real .docx sits
#: well under ~30x overall; 200x still trips classic bombs (1000x+) with a wide
#: margin for XML-heavy documents.
MAX_COMPRESSION_RATIO = 200.0

#: Skip the ratio test for trivially small archives, where a few hundred bytes
#: of ZIP headers make the ratio meaningless.
MIN_COMPRESSED_FOR_RATIO = 4096

#: Chunk size for :func:`stream_zip_member` bounded copies.
_STREAM_CHUNK = 1 << 20  # 1 MiB


def _reject(docx_path: Path | str, reason: str) -> None:
    """Raise the uniform resource-limit rejection error."""
    raise ValueError(f"{docx_path}: unsafe .docx archive -- {reason}")


def _member_name_is_unsafe(name: str) -> bool:
    """True if a ZIP member name is absolute, parent-traversing, or NUL-bearing.

    A well-formed .docx uses only forward-slash relative paths
    (``word/document.xml``); anything absolute (``/etc``, ``C:\\...``),
    parent-traversing (``../``), or containing a NUL is a path-traversal or
    injection attempt, never a real manuscript part.
    """
    if "\x00" in name:
        return True
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:", normalized):  # Windows drive-absolute
        return True
    return ".." in normalized.split("/")


def _normalized_member_key(name: str) -> str:
    """Case-folded, separator-normalized member name for duplicate detection.

    Windows and macOS default filesystems are case-insensitive, so two members
    differing only in case or slash direction collide on extraction; treat them
    as duplicates.
    """
    return name.replace("\\", "/").lower()


def validate_docx_archive(
    docx_path: Path | str,
    *,
    max_member_count: int = MAX_MEMBER_COUNT,
    max_member_expanded_bytes: int = MAX_MEMBER_EXPANDED_BYTES,
    max_total_expanded_bytes: int = MAX_TOTAL_EXPANDED_BYTES,
    max_compression_ratio: float = MAX_COMPRESSION_RATIO,
    min_compressed_for_ratio: int = MIN_COMPRESSED_FOR_RATIO,
) -> None:
    """Reject a .docx archive that violates a resource-safety bound.

    Call this once, before anything decompresses a member. On success it
    returns ``None``; on any violation it raises :class:`ValueError` naming the
    document and the specific bound. A non-ZIP or corrupt file raises the same
    ``"... : not a valid .docx (...)"`` contract the rest of the ingest layer
    uses, so callers need no special-casing.

    Bounds are checked against declared central-directory metadata (member
    sizes, names, encryption flags) without decompressing, so the guard is
    cheap and safe against the very bombs it detects.
    """
    try:
        archive = zipfile.ZipFile(docx_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{docx_path}: not a valid .docx ({exc})") from exc

    with archive:
        infos = archive.infolist()

        if len(infos) > max_member_count:
            _reject(
                docx_path,
                f"declares {len(infos)} members (limit {max_member_count})",
            )

        total_expanded = 0
        total_compressed = 0
        seen_keys: set[str] = set()
        for info in infos:
            # 0x1 in the general-purpose bit flag marks an encrypted member;
            # we cannot inspect an encrypted manuscript and will not try.
            if info.flag_bits & 0x1:
                _reject(docx_path, f"member {info.filename!r} is encrypted")

            if _member_name_is_unsafe(info.filename):
                _reject(docx_path, f"member name {info.filename!r} is unsafe")

            key = _normalized_member_key(info.filename)
            if key in seen_keys:
                _reject(docx_path, f"duplicate member name {info.filename!r}")
            seen_keys.add(key)

            if info.file_size > max_member_expanded_bytes:
                _reject(
                    docx_path,
                    f"member {info.filename!r} expands to {info.file_size} bytes "
                    f"(limit {max_member_expanded_bytes})",
                )

            total_expanded += info.file_size
            total_compressed += info.compress_size

        if total_expanded > max_total_expanded_bytes:
            _reject(
                docx_path,
                f"expands to {total_expanded} bytes total "
                f"(limit {max_total_expanded_bytes})",
            )

        if (
            total_compressed >= min_compressed_for_ratio
            and total_expanded > total_compressed * max_compression_ratio
        ):
            ratio = total_expanded / total_compressed
            _reject(
                docx_path,
                f"compression ratio {ratio:.0f}x exceeds {max_compression_ratio:.0f}x",
            )


def stream_zip_member(
    zin: zipfile.ZipFile,
    zout: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    chunk_size: int = _STREAM_CHUNK,
    max_bytes: int = MAX_MEMBER_EXPANDED_BYTES,
) -> None:
    """Copy one member from ``zin`` to ``zout`` without materialising it.

    Reads and writes in ``chunk_size`` blocks so a large embedded figure is
    never held whole in memory, and stops with a :class:`ValueError` if the
    member's *actual* decompressed size exceeds ``max_bytes`` -- catching an
    archive whose declared header size lied to :func:`validate_docx_archive`.
    """
    if info.is_dir():
        zout.writestr(info.filename, b"")
        return
    total = 0
    with zin.open(info) as src, zout.open(info.filename, "w") as dst:
        while chunk := src.read(chunk_size):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"archive member {info.filename!r} expands past {max_bytes} "
                    f"bytes during copy"
                )
            dst.write(chunk)
