"""Strip embedded metadata from a raster figure already written to the output tree.

Every figure LaTeXtify emits lands in the project's ``figures/`` directory, and
that directory is itself a deliverable -- it goes to arXiv, to a journal's
source-file upload, into the exported ``.zip``. A micrograph or lab photo that
arrives as a passthrough ``.png``/``.jpg`` is copied there byte-for-byte, so
whatever the camera or the acquisition software wrote into it (see
:mod:`latextify.privacy.images` for the threat model: GPS fix, body/lens serial
numbers, the photographer's name, and an EXIF thumbnail that can still show the
*uncropped* original frame) ships with the manuscript.

This module removes that, and is deliberately NOT
:func:`latextify.privacy.images.sanitize`. That function rebuilds the image
from raw pixel bytes, which is the right call for a one-off ``latextify clean``
-- it is the only way to be sure no maker-note survives -- but it re-encodes.
MEASURED (2026-08-10) on a quality-95 JPEG: 44,072 bytes in, 21,770 out, worst
per-channel pixel delta 64/255. Applying that silently to every figure of every
conversion would trade a fidelity regression for privacy nobody asked for --
the same trade the ICC-profile decision in ``privacy/images.py`` refused.

So stripping here works at the *container* level instead: walk the file's
segment/chunk structure, drop the members that hold metadata, and copy
everything else through untouched. The compressed image data is never decoded,
so the emitted figure is pixel-identical to the source, and the colour profile
survives because it is simply never removed.

    JPEG -- APP1 (EXIF and XMP), APP13 (Photoshop/IPTC), the vendor APPn
        blocks, and COM comments are dropped. APP0 (JFIF), APP14 (Adobe colour
        transform -- required to render CMYK/YCCK correctly) and an APP2 ICC
        profile are kept. An APP2 that is *not* ICC is dropped: APP2 also
        carries MPF (Multi-Picture Format), which on phone cameras embeds an
        entire second JPEG of the original frame.

    PNG -- the ancillary text and time chunks (``tEXt``/``zTXt``/``iTXt``,
        ``eXIf``, ``tIME``) are dropped. Everything structural or colorimetric
        (``IHDR``, ``PLTE``, ``IDAT``, ``iCCP``, ``tRNS``, ``gAMA``, ``sRGB``,
        ``cHRM``, ``sBIT``, ``pHYs``, ...) is kept.

:func:`strip_figure_metadata` never raises and never leaves a partial file: a
container it cannot parse keeps its original bytes and comes back as a warning
naming the file, because a figure that quietly fails to convert is worse than
one that says it still carries metadata.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Extensions this module knows how to strip. Matches the raster subset of
#: ``convert.PASSTHROUGH_EXTENSIONS`` -- the formats that can reach the output
#: tree as a byte copy of a file LaTeXtify did not write.
SCRUBBABLE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})

# ── JPEG ────────────────────────────────────────────────────────────────────

_SOI = b"\xff\xd8"
#: Markers that stand alone (no length field): SOI, EOI, TEM, and RST0-RST7.
_STANDALONE = frozenset({0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)})
#: Start of Scan -- everything from here to the end is entropy-coded image data
#: and is copied verbatim.
_SOS = 0xDA
#: APPn/COM markers holding metadata rather than decoding parameters.
_DROP_MARKERS = frozenset({0xE1, 0xED, 0xEF, 0xFE, *range(0xE3, 0xED)})
#: APP2 is kept only when it is an ICC profile chunk; see the module docstring.
_APP2 = 0xE2
_ICC_SIGNATURE = b"ICC_PROFILE\x00"


def _strip_jpeg(data: bytes) -> bytes:
    """Return ``data`` without its metadata segments; raise ValueError if malformed."""
    if not data.startswith(_SOI):
        raise ValueError("not a JPEG (no SOI marker)")
    out = bytearray(_SOI)
    i = 2
    while i + 1 < len(data):
        if data[i] != 0xFF:
            raise ValueError(f"expected a marker at byte {i}")
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte before the real marker
            i += 1
            continue
        if marker in _STANDALONE:
            out += data[i : i + 2]
            i += 2
            continue
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2 or i + 2 + length > len(data):
            raise ValueError(f"segment at byte {i} has an impossible length")
        if marker == _SOS:
            # Scan header + entropy data + EOI, untouched. A progressive JPEG's
            # later scans ride along in that tail, which is correct: metadata
            # segments precede the first SOS.
            out += data[i:]
            return bytes(out)
        payload = data[i + 4 : i + 2 + length]
        drop = marker in _DROP_MARKERS or (
            marker == _APP2 and not payload.startswith(_ICC_SIGNATURE)
        )
        if not drop:
            out += data[i : i + 2 + length]
        i += 2 + length
    # Falling out of the loop means no scan was ever found. Returning what was
    # accumulated would write a plausible-looking truncation over the original.
    raise ValueError("JPEG ended before its scan (SOS) marker")


# ── PNG ─────────────────────────────────────────────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: Ancillary chunks that carry authoring metadata. Every other chunk type --
#: known or not -- is preserved, so an unfamiliar colour/structure chunk is
#: never silently discarded.
_DROP_CHUNKS = frozenset({b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"})


def _strip_png(data: bytes) -> bytes:
    """Return ``data`` without its metadata chunks; raise ValueError if malformed."""
    if not data.startswith(_PNG_MAGIC):
        raise ValueError("not a PNG (bad signature)")
    out = bytearray(_PNG_MAGIC)
    i = len(_PNG_MAGIC)
    while i + 8 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        chunk_type = data[i + 4 : i + 8]
        end = i + 12 + length  # length + type + payload + CRC
        if end > len(data):
            raise ValueError(f"chunk {chunk_type!r} runs past the end of the file")
        if chunk_type not in _DROP_CHUNKS:
            out += data[i:end]
        i = end
        if chunk_type == b"IEND":
            return bytes(out)
    raise ValueError("PNG ended without an IEND chunk")


#: extension -> (the format's magic bytes, its stripper). The magic is checked
#: BEFORE stripping so the two failure modes stay distinguishable: a file whose
#: signature is wrong is not this format at all, so it holds no metadata to
#: leak and warrants no privacy warning (a figure that is not really an image
#: fails the compile step, which says so far better than this module could). A
#: file with the right signature that then fails to parse is a real image whose
#: metadata may well have survived -- that one is worth a warning.
_STRIPPERS = {
    ".jpg": (_SOI, _strip_jpeg),
    ".jpeg": (_SOI, _strip_jpeg),
    ".png": (_PNG_MAGIC, _strip_png),
}


def strip_figure_metadata(path: Path) -> str | None:
    """Rewrite ``path`` in place without its embedded metadata.

    Returns ``None`` when the file was cleaned (or needed no cleaning, or is
    not a format this module handles), and a human-readable warning otherwise.
    Never raises: a figure that cannot be stripped is left exactly as it was,
    which keeps the conversion working and makes the residual metadata the
    caller's problem to report rather than a silent leak.
    """
    entry = _STRIPPERS.get(path.suffix.lower())
    if entry is None or not path.is_file():
        return None
    magic, stripper = entry
    # Written beside the target and renamed, so an interrupted write can never
    # leave figures/ holding a truncated image.
    tmp = path.with_name(f"{path.name}.scrub-tmp")
    try:
        original = path.read_bytes()
        if not original.startswith(magic):
            return None  # not this format; nothing here can be carrying metadata
        stripped = stripper(original)
        if stripped == original:
            return None  # nothing to remove; leave the file's mtime alone
        tmp.write_bytes(stripped)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        tmp.unlink(missing_ok=True)
        return (
            f"could not strip embedded metadata from {path.name} ({exc}); it was "
            "included unchanged, so any camera, GPS or authoring data it carries is "
            "still present. Run `latextify inspect` on it to see what that is."
        )
    return None
