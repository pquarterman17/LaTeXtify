"""Raster figure preparation: alpha flattening, display crop, TIFF -> PNG.

Split out of :mod:`latextify.figures.convert` (2026-08-10). Everything here
opens the image with Pillow and writes pixels; the vector formats live in
:mod:`latextify.figures.vector` and never touch this path.

Two rules this module holds, both learned from real manuscripts:

- **Transparency is flattened onto white.** A transparent raster has no
  defined backdrop inside a PDF, which surfaces as faint halo lines bordering
  the figure. Journals expect opaque figures regardless.
- **A failed TIFF conversion writes nothing.** Falling back to copying the raw
  .tif through would silently reintroduce the exact compile failure the
  conversion exists to prevent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from latextify.figures.crop import CROP_NOTE, apply_crop, wants_crop
from latextify.figures.outcome import ConversionOutcome
from latextify.model.figure import CropRect

# --------------------------------------------------------------------------- #
# Alpha flattening (shared by passthrough rasters and the TIFF->PNG path)
# --------------------------------------------------------------------------- #


def _has_alpha(image) -> bool:  # noqa: ANN001 -- PIL.Image.Image, imported lazily
    """True if ``image`` carries transparency that must be flattened.

    Covers the direct alpha modes (``RGBA``/``LA``/``PA``) and the palette
    case where the alpha lives in a ``transparency`` info entry rather than a
    band (a ``P``-mode PNG). ``RGB``/``L``/``P``-without-transparency return
    ``False`` so a fully-opaque image is never needlessly re-encoded.
    """
    return image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )


def _flatten_alpha_onto_white(image):  # noqa: ANN001, ANN201 -- PIL types, lazy import
    """Composite any alpha channel onto opaque white; return an alpha-free image.

    A transparent raster has no defined backdrop inside a PDF -- xdvipdfmx
    renders its transparent pixels against nothing, which surfaces as faint
    halo/edge lines bordering the figure (observed on a real manuscript's one
    RGBA PNG: "weird lines around the top and bottom"). Journals expect opaque
    figures regardless. Partial alpha (anti-aliased edges) is composited
    correctly rather than hard-cut, and the transparent pixels' underlying RGB
    -- often garbage -- is discarded in favour of white. An image with no alpha
    is returned unchanged.
    """
    from PIL import Image

    if not _has_alpha(image):
        return image
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def prepare_passthrough_raster(
    src: Path, dest: Path, crop: CropRect | None
) -> ConversionOutcome | None:
    """Crop and/or alpha-flatten a passthrough raster, writing ``dest``.

    Returns a :class:`ConversionOutcome` when the image was re-encoded (a crop
    was applied and/or transparency flattened), or ``None`` to tell the caller
    "nothing to do -- plain-copy the bytes" (no crop requested and no alpha).
    Never raises: an unreadable/exotic raster falls back to a byte-for-byte copy
    -- but if a crop was requested and could not be applied, that copy is
    reported with a warning (the hidden region survives), never silently.
    """
    from PIL import Image

    want_crop = wants_crop(crop)
    try:
        with Image.open(src) as image:
            has_alpha = _has_alpha(image)
            if not want_crop and not has_alpha:
                return None  # common fast path: nothing to change, caller copies
            prepared = apply_crop(image, crop) if want_crop else image
            note = CROP_NOTE if want_crop else None
            flattened = _flatten_alpha_onto_white(prepared)
            if flattened is not prepared:  # alpha was present and composited
                note = (
                    f"{note} Flattened image transparency onto a white background."
                    if note
                    else "Flattened image transparency onto a white background."
                )
            flattened.save(dest)
    except Exception:
        dest.unlink(missing_ok=True)  # discard any partial write
        if want_crop:
            # A crop was asked for but Pillow couldn't process the image; still
            # produce the figure (copy) but say the hidden region wasn't removed.
            shutil.copy2(src, dest)
            return ConversionOutcome(
                dest_path=dest,
                warning=(
                    f"could not crop {src.name} to its visible region (Pillow could not "
                    "process it); it was included uncropped, so any content Word cropped "
                    "out is still present."
                ),
            )
        return None  # no crop wanted: caller plain-copies the bytes
    return ConversionOutcome(dest_path=dest, note=note)


# --------------------------------------------------------------------------- #
# TIFF -> PNG (Pillow)
# --------------------------------------------------------------------------- #


def _pillow_convert(src: Path, dest: Path, crop: CropRect | None = None) -> None:
    """Thin wrapper around Pillow's TIFF->PNG conversion -- its own call
    point for the same monkeypatch-testability reason as ``_cairosvg_convert``
    / ``_ghostscript_convert``."""
    from PIL import Image

    with Image.open(src) as image:
        # TIFF commonly carries modes PNG can't encode directly (CMYK,
        # 16-bit-per-channel "I;16", palette-with-transparency edge cases);
        # normalize to RGB/RGBA so the PNG write never fails on mode alone.
        if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        # Trim Word's display crop first so the hidden region is gone before the
        # PNG is written (privacy/fidelity), then flatten any transparency onto
        # white for the same reason as the passthrough path.
        if wants_crop(crop):
            image = apply_crop(image, crop)
        image = _flatten_alpha_onto_white(image)
        image.save(dest, format="PNG")


_TIFF_UNSUPPORTED_NOTE = (
    "Tectonic cannot include TIFF files (xdvipdfmx has no TIFF support; a raw "
    'TIFF \\includegraphics fails with "Cannot determine size of graphic").'
)


def convert_tiff(
    src: Path, dest_dir: Path, number: int, *, prefix: str = "", crop: CropRect | None = None
) -> ConversionOutcome:
    """Convert ``src`` (a .tif/.tiff) to PNG via Pillow.

    Unlike :func:`convert_svg`/:func:`convert_eps`, a failed conversion does
    NOT fall back to copying the raw TIFF through -- that would silently
    reintroduce the exact compile failure this function exists to prevent.
    Instead nothing is written at ``dest`` and the returned warning names the
    file and the fix. Never raises: Pillow surfaces a corrupt/unreadable TIFF
    through several exception types (``OSError``, ``UnidentifiedImageError``
    -- a subclass of ``OSError`` -- ``ValueError``), all caught here.

    ``crop`` (when effective) is applied during the conversion so the TIFF's
    Word-cropped region never survives into the emitted PNG.
    """
    dest = dest_dir / f"fig{prefix}{number}.png"
    try:
        _pillow_convert(src, dest, crop)
    except Exception as exc:  # Pillow's failure modes vary; never crash the emit
        dest.unlink(missing_ok=True)  # clean up any partial/truncated write
        return ConversionOutcome(
            dest_path=dest,
            warning=(
                f"{_TIFF_UNSUPPORTED_NOTE} Conversion to PNG via Pillow failed for "
                f"{src.name} ({exc}); no file was written to figures/{dest.name} -- "
                "verify the TIFF isn't corrupt, or supply a pre-converted PNG via "
                "figures.yaml or a folder override."
            ),
        )
    note = "TIFF converted to PNG via Pillow."
    if wants_crop(crop):
        note += " " + CROP_NOTE
    return ConversionOutcome(dest_path=dest, note=note)
