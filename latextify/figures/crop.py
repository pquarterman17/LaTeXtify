"""Word image crop (``a:srcRect``) reading and application.

Word crops an image for *display* via a DrawingML ``a:srcRect`` but keeps the
full original pixels embedded in ``word/media/``; nothing downstream applies the
crop, so the hidden regions otherwise leak into the extracted figure and the
compiled PDF (FORMATS_AND_PRIVACY_PLAN item 2). This module owns both halves of
the fix:

    reading  -- :func:`image_crops` parses ``word/document.xml`` for each
        main-flow picture's ``srcRect`` and :func:`attach_crops` binds it to the
        matching :class:`~latextify.model.figure.Figure`.
    applying -- :func:`apply_crop` trims a Pillow image to a crop's visible
        region; :mod:`latextify.figures.convert` calls it on the way through.

The :class:`~latextify.model.figure.CropRect` IR itself lives in
:mod:`latextify.model.figure`. A vector/PDF figure cannot be raster-cropped, so
the convert stage surfaces :func:`uncroppable_message` instead of silently
leaving the hidden region in place.
"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from latextify.model.figure import CropRect, Figure

# officeDocument relationships namespace: <a:blip r:embed="rIdN"> keys the
# picture to a relationship in word/_rels/document.xml.rels.
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# DrawingML srcRect values are expressed in thousandths of a percent
# (100000 == 100%); dividing by this yields a 0.0-1.0 fraction.
_SRCRECT_FULL = 100000.0


# --------------------------------------------------------------------------- #
# Reading a:srcRect from the docx and binding it to figures
# --------------------------------------------------------------------------- #


def _localname(tag: str) -> str:
    """The local (namespace-stripped) name of an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _rid_to_image_basename(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map each image relationship id -> its media file basename.

    Reads ``word/_rels/document.xml.rels``; only ``.../image`` relationships are
    kept. A missing/broken rels part yields an empty map (the caller then simply
    can't verify a blip's target, and degrades to no crop), never an error.
    """
    try:
        root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return {}
    mapping: dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        if rid and "image" in (rel.get("Type") or "").lower():
            mapping[rid] = Path(rel.get("Target") or "").name
    return mapping


def _collect_blipfills(elem: ET.Element, out: list[ET.Element]) -> None:
    """Preorder-collect every picture ``blipFill`` in the MAIN document flow.

    Descends the tree but never into a ``w:txbxContent`` (text box) -- those hold
    captions, not figure images, and pandoc drops them, so a blip there has no
    matching ``Figure`` and would misalign the document-order association. An
    ``mc:Fallback`` subtree is skipped too: it is the legacy VML alternative to
    the ``mc:Choice`` DrawingML picture pandoc actually reads, so counting it
    would double-count the image.
    """
    tag = _localname(elem.tag)
    if tag in ("txbxContent", "Fallback"):
        return
    if tag == "blipFill":
        out.append(elem)
    for child in elem:
        _collect_blipfills(child, out)


def _parse_srcrect(elem: ET.Element) -> CropRect | None:
    """Parse an ``a:srcRect`` element into a ``CropRect`` (or ``None``).

    Each of ``l``/``t``/``r``/``b`` is a thousandths-of-a-percent inset from that
    edge; a missing attribute is 0. Negative values (an outset/padding, meaning
    the whole image shows) clamp to 0 so we never reveal more than the original.
    Returns ``None`` for a no-op crop or a degenerate one (insets that would
    leave zero width/height), so only a real, applicable crop is carried.
    """

    def frac(attr: str) -> float:
        try:
            value = int(elem.get(attr, "0")) / _SRCRECT_FULL
        except (TypeError, ValueError):
            return 0.0
        return value if value > 0.0 else 0.0  # clamp outset/padding to no-crop

    left, top, right, bottom = frac("l"), frac("t"), frac("r"), frac("b")
    if left + right >= 1.0 or top + bottom >= 1.0:
        return None  # degenerate rectangle -> treat as no crop
    crop = CropRect(left=left, top=top, right=right, bottom=bottom)
    return crop if crop.is_effective() else None


def _blipfill_crop(
    blipfill: ET.Element, rid_to_basename: dict[str, str]
) -> tuple[str | None, CropRect | None]:
    """Extract ``(media basename, crop)`` for one ``blipFill``.

    The basename comes from the child ``a:blip``'s ``r:embed`` relationship; the
    crop from a child ``a:srcRect`` (absent -> ``None``). Either may be ``None``
    (an unresolved rId, or no crop) -- the caller handles both.
    """
    basename: str | None = None
    crop: CropRect | None = None
    for child in blipfill:
        local = _localname(child.tag)
        if local == "blip":
            rid = child.get(_R + "embed") or child.get(_R + "link")
            if rid is not None:
                basename = rid_to_basename.get(rid)
        elif local == "srcRect":
            crop = _parse_srcrect(child)
    return basename, crop


def image_crops(docx_path: Path) -> tuple[tuple[str | None, CropRect | None], ...]:
    """Ordered ``(basename, crop)`` for every main-flow picture in the docx.

    Document order matches the ``Image``-node order ``extract_figures`` numbers
    figures by (pandoc reads the same body in the same order), so the Nth entry
    corresponds to figure N -- cross-checked by basename in :func:`attach_crops`.
    A docx that can't be read yields an empty tuple: a pure fallback, exactly
    like ``latextify.figures.extract._textbox_captions``, so a broken/exotic
    package never fails the emit -- it just means no crops are applied.
    """
    try:
        with zipfile.ZipFile(docx_path) as archive:
            rid_to_basename = _rid_to_image_basename(archive)
            root = ET.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return ()
    blipfills: list[ET.Element] = []
    _collect_blipfills(root, blipfills)
    return tuple(_blipfill_crop(bf, rid_to_basename) for bf in blipfills)


def _unique_effective_crops(
    ordered_crops: tuple[tuple[str | None, CropRect | None], ...],
) -> dict[str, CropRect]:
    """Map basename -> crop, but ONLY for basenames with one unambiguous crop.

    The fallback association when document order can't be verified: a media file
    referenced once (or several times but always cropped identically) maps to
    that single crop; one cropped two different ways is dropped rather than
    guessed (better a residual leak than a wrong crop on the wrong figure).
    """
    by_basename: dict[str, set[CropRect]] = {}
    for basename, crop in ordered_crops:
        if basename is None or crop is None:
            continue
        by_basename.setdefault(basename, set()).add(crop)
    return {name: next(iter(crops)) for name, crops in by_basename.items() if len(crops) == 1}


def attach_crops(
    figures: tuple[Figure, ...],
    ordered_crops: tuple[tuple[str | None, CropRect | None], ...],
) -> tuple[Figure, ...]:
    """Attach each parsed ``srcRect`` crop to its ``Figure``.

    Primary path: positional (Nth crop -> Nth figure), accepted only when the
    counts match and every resolvable basename agrees with that figure's
    extracted media file -- so a mismatch can never crop the wrong figure. This
    is the path that also handles the same image cropped differently in two
    figures. If that verification fails, fall back to
    :func:`_unique_effective_crops` (basename-keyed, unambiguous crops only).
    Figures untouched when no effective crop exists anywhere (the common case).
    """
    if not any(crop is not None for _basename, crop in ordered_crops):
        return figures

    resolvable = [
        (basename, crop, figure)
        for (basename, crop), figure in zip(ordered_crops, figures, strict=False)
        if basename is not None
    ]
    aligned = (
        len(ordered_crops) == len(figures)
        and bool(resolvable)
        and all(basename == figure.embedded_path.name for basename, _c, figure in resolvable)
    )
    if aligned:
        # aligned implies equal lengths, so strict=True documents that invariant.
        return tuple(
            replace(figure, crop=crop) if crop is not None else figure
            for (_basename, crop), figure in zip(ordered_crops, figures, strict=True)
        )

    by_basename = _unique_effective_crops(ordered_crops)
    return tuple(
        replace(figure, crop=by_basename[figure.embedded_path.name])
        if figure.embedded_path.name in by_basename
        else figure
        for figure in figures
    )


# --------------------------------------------------------------------------- #
# Applying a crop to a raster (called from latextify.figures.convert)
# --------------------------------------------------------------------------- #


def wants_crop(crop: CropRect | None) -> bool:
    """True when ``crop`` is present and actually hides pixels."""
    return crop is not None and crop.is_effective()


def apply_crop(image, crop: CropRect):  # noqa: ANN001, ANN201 -- PIL types, lazy import
    """Return ``image`` cropped to ``crop``'s visible region (a new image).

    Converts the fractional per-edge insets to a pixel box and crops. A box that
    would be degenerate (zero/negative width or height after rounding) or a
    whole-image no-op returns the input unchanged, so this never produces an
    empty image.
    """
    width, height = image.size
    left = max(0, min(width, round(crop.left * width)))
    top = max(0, min(height, round(crop.top * height)))
    right = max(0, min(width, round((1.0 - crop.right) * width)))
    bottom = max(0, min(height, round((1.0 - crop.bottom) * height)))
    if right - left < 1 or bottom - top < 1 or (left, top, right, bottom) == (0, 0, width, height):
        return image
    return image.crop((left, top, right, bottom))


#: Report-facing note recorded on a figure whose raster was cropped.
CROP_NOTE = "Cropped image to its visible region (removed Word's hidden crop area)."


def uncroppable_message(kind: str, name: str) -> str:
    """Warning text for a crop that couldn't be raster-applied (vector/PDF)."""
    return (
        f"{name} was cropped in Word (a:srcRect) but it is a {kind} figure, which "
        "LaTeXtify does not crop -- the cropped-out region may still be present in the "
        "output. Supply a pre-cropped raster (figures.yaml or a folder override) if that "
        "hidden content is sensitive."
    )
