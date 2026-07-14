"""SVG->PDF, EPS->PDF, and TIFF->PNG conversion for LaTeX inclusion (plan item 15).

Tectonic (see ``latextify.compile.tectonic``) is built on a XeTeX-derived
engine whose ``xdvipdfmx`` PDF backend embeds PDF/PNG/JPEG directly but has
no PostScript OR TIFF image support at all. Three of the formats
``latextify.figures.override`` can resolve to therefore need conversion
before they can land in the output tree's ``figures/`` directory as
something Tectonic can actually ``\\includegraphics``:

    SVG -- always converted to PDF. Two converters are tried in order:

        1. cairosvg (best fidelity; wraps native libcairo). VERIFIED
           (2026-07-11, item 15) on this project's Windows dev machine:
           cairosvg *imports* fine, but ``svg2pdf`` raises ``OSError`` at
           call time because no ``libcairo-2.dll`` is present -- cairosvg is
           a ctypes binding, not a self-contained wheel, so `pip`/`uv`
           installing the Python package does not install the GTK/cairo
           native runtime it needs. Both ``ImportError`` (package not
           installed at all) and ``OSError`` (DLL missing) are caught.
        2. svglib + reportlab (pure-Python fallback; a required dependency,
           see pyproject.toml, so this path always works). VERIFIED working
           on the same machine. Lower fidelity than cairosvg (documented
           upstream): gradients, filter effects, and some clipping paths do
           not render identically. The resulting note says so explicitly so
           it can flow into the item 16 consolidated report as a "verify
           me", per the plan text ("record a fidelity-limits note").

    EPS -- VERIFIED empirically (2026-07-11, item 15): compiling
        ``\\includegraphics{fig.eps}`` under Tectonic fails with "sorry,
        PostScript images are not supported by Tectonic" / "pdf: image
        inclusion failed" (see test_figures_convert.py::test_tectonic_...
        for the reproduction, marked ``tectonic``). So EPS is converted via
        Ghostscript (``gs``/``gswin64c``/``gswin32c``, whichever is found on
        PATH) when available; when it is not (the case on this dev
        machine -- no Ghostscript install), the source file is copied
        through unchanged (so *something* exists at the expected path) and
        an actionable :class:`~latextify.model.emit.EmitWarning`-worthy
        message is returned naming the fix.

    TIFF -- converted to PNG via Pillow, a required dependency (see
        pyproject.toml) -- Word embeds TIFF constantly (scanner/microscope
        exports commonly land in a manuscript as .tif/.tiff), and a raw
        ``\\includegraphics{fig.tiff}`` fails Tectonic with "Cannot determine
        size of graphic" (a real manuscript conversion failure this way is
        what motivated this conversion path). Unlike the EPS path above,
        a failed TIFF conversion does NOT fall back to copying the raw
        ``.tif``/``.tiff`` into the output tree -- that would silently
        reintroduce the exact same compile failure it exists to prevent.
        Instead nothing is written at the expected path and an actionable
        :class:`~latextify.model.emit.EmitWarning`-worthy message names the
        file and the fix (verify the TIFF isn't corrupt, or supply a
        pre-converted PNG via figures.yaml or a folder override).

    PDF/PNG/JPG/JPEG -- pass through unchanged (Tectonic embeds all of
        these natively; no conversion needed).

:func:`convert_for_latex` is the entry point, called at emit time from
``latextify.emit.project._copy_figures`` -- the converted (or
passed-through) file lands directly in the output tree's ``figures/`` as
``fig<N>.pdf`` (or ``fig<N><original-ext>`` for untouched passthrough
formats, or as a last-resort copy when conversion could not happen at all).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from latextify.figures.crop import CROP_NOTE, apply_crop, uncroppable_message, wants_crop
from latextify.model.figure import CropRect

#: Extensions always converted to PDF before inclusion.
SVG_EXTENSIONS = frozenset({".svg"})
EPS_EXTENSIONS = frozenset({".eps"})
#: Extensions always converted to PNG before inclusion.
TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})
#: Extensions Tectonic embeds natively -- copied through unchanged.
PASSTHROUGH_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
#: The raster subset of the passthrough formats -- these are opened with
#: Pillow on the way through so any alpha channel can be flattened onto white
#: (see ``_flatten_passthrough_raster``). PDF is excluded (it is not a raster
#: and must never be handed to Pillow); JPEG has no alpha channel but is
#: included harmlessly (the flatten check is a no-op for it).
_RASTER_PASSTHROUGH_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})

#: Ghostscript executable names to probe for, in order (Windows ships
#: `gswin64c`/`gswin32c`; POSIX systems ship `gs`).
_GHOSTSCRIPT_CANDIDATES = ("gs", "gswin64c", "gswin32c")


@dataclass(frozen=True)
class ConversionOutcome:
    """Result of preparing one figure file for LaTeX inclusion.

    ``dest_path`` is always the file actually written into the output
    tree's ``figures/`` directory -- for passthrough formats this is a copy
    of the source; for a converted format it is the new PDF; for a failed
    conversion it is a last-resort copy of the original (so the compile
    fails with a normal "file not found for that format" error rather than
    a missing-file error, and the reason is in ``warning``).

    ``note`` is a short, human-readable description of a conversion that
    *succeeded* but is worth recording (e.g. which converter ran, fidelity
    caveats) -- meant to flow onto the ``Figure`` IR's ``conversion_note``
    field and, eventually, the item 16 consolidated report. ``None`` when
    nothing noteworthy happened (plain passthrough).

    ``warning`` is set instead of ``note`` when conversion could not happen
    at all; the caller surfaces it as an
    :class:`~latextify.model.emit.EmitWarning`. Never set together with
    ``note``.
    """

    dest_path: Path
    note: str | None = None
    warning: str | None = None


def convert_for_latex(
    src: Path, dest_dir: Path, number: int, *, prefix: str = "", crop: CropRect | None = None
) -> ConversionOutcome:
    """Prepare figure ``number``'s resolved file ``src`` for inclusion in ``dest_dir``.

    Dispatches purely on ``src``'s extension: SVG is always converted to
    PDF, EPS is converted via Ghostscript when available (else passed
    through with a warning), TIFF is always converted to PNG via Pillow
    (else nothing is written, see :func:`_convert_tiff`), and everything
    else (PDF/PNG/JPG/JPEG, or any other extension the override tiers
    happened to resolve to) is copied through unchanged as
    ``fig<prefix><number><ext>``.

    ``prefix`` (plan item 21) defaults to ``""``; a supplementary document's
    figures pass ``prefix="S"`` so they land as ``figS<number>.<ext>`` in the
    shared ``figures/`` directory, never colliding with the main document's
    ``fig<number>.<ext>``.

    ``crop`` (FORMATS_AND_PRIVACY_PLAN item 2) is the Word display crop
    (``a:srcRect``) for this image. When set and effective it is applied to the
    raster on the way through -- for a passthrough PNG/JPEG and for the TIFF->PNG
    path -- so the pixels Word cropped OUT never reach the output tree. A crop on
    a vector (SVG/EPS) or PDF figure cannot be raster-applied, so it degrades to
    a warning rather than silently leaving the hidden regions in place.
    """
    ext = src.suffix.lower()
    if ext in SVG_EXTENSIONS:
        return _note_uncroppable(_convert_svg(src, dest_dir, number, prefix=prefix), crop, "SVG")
    if ext in EPS_EXTENSIONS:
        return _note_uncroppable(_convert_eps(src, dest_dir, number, prefix=prefix), crop, "EPS")
    if ext in TIFF_EXTENSIONS:
        return _convert_tiff(src, dest_dir, number, prefix=prefix, crop=crop)
    dest = dest_dir / f"fig{prefix}{number}{ext}"
    if ext in _RASTER_PASSTHROUGH_EXTENSIONS:
        prepared = _prepare_passthrough_raster(src, dest, crop)
        if prepared is not None:
            return prepared
    shutil.copy2(src, dest)
    # A non-raster passthrough (PDF) that Word cropped: the copy carries the
    # full page, so surface that the hidden region could not be trimmed.
    if ext not in _RASTER_PASSTHROUGH_EXTENSIONS and wants_crop(crop):
        return ConversionOutcome(dest_path=dest, warning=uncroppable_message("PDF", src.name))
    return ConversionOutcome(dest_path=dest)


# --------------------------------------------------------------------------- #
# Image cropping (a:srcRect) -- the geometry/reading lives in
# latextify.figures.crop; this only folds an "uncroppable" caveat into a
# ConversionOutcome (which is defined here, so it can't move to that module).
# --------------------------------------------------------------------------- #


def _note_uncroppable(
    outcome: ConversionOutcome, crop: CropRect | None, kind: str
) -> ConversionOutcome:
    """Fold an "uncroppable vector/PDF" warning into a conversion outcome.

    A vector conversion that otherwise succeeded (note set) is downgraded to a
    warning -- an unapplied crop that may leak content is worth flagging over the
    conversion note. An outcome that already failed keeps its warning, with the
    crop caveat appended so neither signal is lost.
    """
    if not wants_crop(crop):
        return outcome
    message = uncroppable_message(kind, outcome.dest_path.name)
    if outcome.warning:
        return replace(outcome, warning=f"{outcome.warning} {message}")
    return replace(outcome, warning=message, note=None)


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


def _prepare_passthrough_raster(
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
# SVG -> PDF
# --------------------------------------------------------------------------- #


def _cairosvg_convert(src: Path, dest: Path) -> None:
    """Thin wrapper around cairosvg's ``svg2pdf`` -- its own call point so
    tests can monkeypatch success/failure without depending on whether the
    real machine running the test happens to have libcairo installed."""
    import cairosvg  # optional dependency; see pyproject.toml's `cairo` extra

    cairosvg.svg2pdf(url=str(src), write_to=str(dest))


def _svglib_convert(src: Path, dest: Path) -> None:
    """Pure-Python SVG->PDF fallback (svglib + reportlab), a required dependency."""
    from reportlab.graphics import renderPDF
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(str(src))
    renderPDF.drawToFile(drawing, str(dest))


def _convert_svg(src: Path, dest_dir: Path, number: int, *, prefix: str = "") -> ConversionOutcome:
    dest = dest_dir / f"fig{prefix}{number}.pdf"

    # NOTE: an `except ... as name:` binding is implicitly deleted at the end
    # of its own except block (Python 3 scoping), so the message is copied
    # into a plain string here -- it needs to survive into the fallback
    # branch below, both on success (as a caveat note) and on double failure.
    cairo_error: str | None = None
    try:
        _cairosvg_convert(src, dest)
        return ConversionOutcome(dest_path=dest, note="SVG converted to PDF via cairosvg.")
    except (ImportError, OSError) as exc:
        cairo_error = str(exc)

    try:
        _svglib_convert(src, dest)
    except Exception as svglib_exc:  # last resort: svglib/reportlab failed too
        svg_dest = dest_dir / f"fig{prefix}{number}.svg"
        shutil.copy2(src, svg_dest)
        return ConversionOutcome(
            dest_path=svg_dest,
            warning=(
                f"SVG to PDF conversion failed with both cairosvg ({cairo_error}) and "
                f"svglib+reportlab ({svglib_exc}); Tectonic cannot include a raw SVG "
                "file -- fix the SVG source or supply a pre-converted PDF via "
                "figures.yaml or a folder override."
            ),
        )

    return ConversionOutcome(
        dest_path=dest,
        note=(
            "SVG converted to PDF via svglib+reportlab fallback "
            f"(cairosvg unavailable: {cairo_error}). Fidelity limits apply: complex "
            "gradients, filter effects, and some clipping paths may not render "
            "identically to the source SVG -- verify the output PDF visually."
        ),
    )


# --------------------------------------------------------------------------- #
# EPS -> PDF (Ghostscript) / actionable warning
# --------------------------------------------------------------------------- #


def _find_ghostscript() -> str | None:
    for name in _GHOSTSCRIPT_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _ghostscript_convert(gs_binary: str, src: Path, dest: Path) -> None:
    """Thin wrapper around the Ghostscript invocation -- its own call point
    for the same monkeypatch-testability reason as ``_cairosvg_convert``."""
    subprocess.run(
        [
            gs_binary,
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-dEPSCrop",
            "-sDEVICE=pdfwrite",
            f"-sOutputFile={dest}",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


_EPS_UNSUPPORTED_NOTE = (
    "Tectonic cannot include EPS files directly (verified: its xdvipdfmx PDF "
    "backend reports 'PostScript images are not supported by Tectonic')."
)


def _convert_eps(src: Path, dest_dir: Path, number: int, *, prefix: str = "") -> ConversionOutcome:
    gs_binary = _find_ghostscript()
    if gs_binary is None:
        dest = dest_dir / f"fig{prefix}{number}.eps"
        shutil.copy2(src, dest)
        return ConversionOutcome(
            dest_path=dest,
            warning=(
                f"{_EPS_UNSUPPORTED_NOTE} No Ghostscript (gs/gswin64c/gswin32c) was "
                "found on PATH to convert it to PDF. Install Ghostscript and re-run, "
                "or supply a PDF version via figures.yaml or a folder override."
            ),
        )

    dest = dest_dir / f"fig{prefix}{number}.pdf"
    try:
        _ghostscript_convert(gs_binary, src, dest)
    except (subprocess.CalledProcessError, OSError) as exc:
        eps_dest = dest_dir / f"fig{prefix}{number}.eps"
        shutil.copy2(src, eps_dest)
        return ConversionOutcome(
            dest_path=eps_dest,
            warning=(
                f"{_EPS_UNSUPPORTED_NOTE} Ghostscript was found but the conversion to "
                f"PDF failed ({exc}); fix the Ghostscript install/EPS source or supply "
                "a PDF version via figures.yaml or a folder override."
            ),
        )
    return ConversionOutcome(dest_path=dest, note="EPS converted to PDF via Ghostscript.")


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
    "TIFF \\includegraphics fails with \"Cannot determine size of graphic\")."
)


def _convert_tiff(
    src: Path, dest_dir: Path, number: int, *, prefix: str = "", crop: CropRect | None = None
) -> ConversionOutcome:
    """Convert ``src`` (a .tif/.tiff) to PNG via Pillow.

    Unlike :func:`_convert_svg`/:func:`_convert_eps`, a failed conversion does
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
