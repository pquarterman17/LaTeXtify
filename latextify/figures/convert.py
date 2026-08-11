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

    EMF/WMF -- Windows metafiles, Word's native vector format for a pasted
        chart or drawing. Converted to PDF by whichever of LibreOffice or
        Inkscape is found on PATH -- an OPTIONAL, DETECTED converter, exactly
        like Ghostscript above and for the same reason: the offline install
        kit bundles only pandoc + Tectonic, and a heavyweight external app
        could not ride along in it (GUI_OPTIONS_FORMATS_PLAN item 11, owner
        gate resolved 2026-08-10). With no converter present, nothing is
        written and the warning names the fix -- the TIFF rule, not the EPS
        one, because copying a metafile through reintroduces exactly the
        compile failure this path prevents. Before this existed that is
        precisely what happened: an .emf was copied to figures/fig<N>.emf
        with no note and no warning, and the compile died on it.

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
from dataclasses import replace
from pathlib import Path

from latextify.figures.crop import uncroppable_message, wants_crop
from latextify.figures.outcome import ConversionOutcome
from latextify.figures.raster import convert_tiff, prepare_passthrough_raster
from latextify.figures.scrub import strip_figure_metadata
from latextify.figures.vector import convert_eps, convert_metafile, convert_svg
from latextify.model.figure import CropRect

#: Extensions always converted to PDF before inclusion.
SVG_EXTENSIONS = frozenset({".svg"})
EPS_EXTENSIONS = frozenset({".eps"})
#: Windows metafiles -- Word's native vector format for pasted charts and
#: drawings. Converted to PDF when a converter is on PATH; see
#: :func:`latextify.figures.vector.convert_metafile`.
METAFILE_EXTENSIONS = frozenset({".emf", ".wmf"})
#: Extensions always converted to PNG before inclusion.
TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})
#: Extensions Tectonic embeds natively -- copied through unchanged.
PASSTHROUGH_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
#: The raster subset of the passthrough formats -- these are opened with
#: Pillow on the way through so any alpha channel can be flattened onto white
#: (see :func:`latextify.figures.raster.prepare_passthrough_raster`). PDF is
#: excluded (it is not a raster and must never be handed to Pillow); JPEG has
#: no alpha channel but is included harmlessly (the flatten check no-ops).
_RASTER_PASSTHROUGH_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})


def convert_for_latex(
    src: Path,
    dest_dir: Path,
    number: int,
    *,
    prefix: str = "",
    crop: CropRect | None = None,
    strip_metadata: bool = True,
) -> ConversionOutcome:
    """Prepare figure ``number``'s resolved file ``src`` for inclusion in ``dest_dir``.

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

    ``strip_metadata`` (METADATA_PRIVACY item 13) defaults to on: whatever
    raster lands in ``dest_dir`` has its EXIF/GPS/serial numbers/thumbnail and
    PNG text chunks removed in place, losslessly -- see
    :mod:`latextify.figures.scrub`. It is the one step here that can be turned
    off (``latextify convert --keep-figure-metadata``), because it is the one
    that removes information an author may have put there deliberately.
    """
    outcome = _dispatch(src, dest_dir, number, prefix=prefix, crop=crop)
    if not strip_metadata:
        return outcome
    problem = strip_figure_metadata(outcome.dest_path)
    if problem is None:
        return outcome
    return replace(outcome, warning=f"{outcome.warning} {problem}" if outcome.warning else problem)


def _dispatch(
    src: Path, dest_dir: Path, number: int, *, prefix: str = "", crop: CropRect | None = None
) -> ConversionOutcome:
    """Route ``src`` to its converter purely on extension: SVG always to PDF,
    EPS via Ghostscript when available (else passed through with a warning),
    TIFF always to PNG via Pillow (else nothing is written, see
    :func:`convert_tiff`), and everything else (PDF/PNG/JPG/JPEG, or any other
    extension the override tiers resolved to) copied through unchanged as
    ``fig<prefix><number><ext>``."""
    ext = src.suffix.lower()
    if ext in SVG_EXTENSIONS:
        return _note_uncroppable(convert_svg(src, dest_dir, number, prefix=prefix), crop, "SVG")
    if ext in EPS_EXTENSIONS:
        return _note_uncroppable(convert_eps(src, dest_dir, number, prefix=prefix), crop, "EPS")
    if ext in METAFILE_EXTENSIONS:
        return _note_uncroppable(
            convert_metafile(src, dest_dir, number, prefix=prefix), crop, "EMF/WMF"
        )
    if ext in TIFF_EXTENSIONS:
        return convert_tiff(src, dest_dir, number, prefix=prefix, crop=crop)
    dest = dest_dir / f"fig{prefix}{number}{ext}"
    if ext in _RASTER_PASSTHROUGH_EXTENSIONS:
        prepared = prepare_passthrough_raster(src, dest, crop)
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
