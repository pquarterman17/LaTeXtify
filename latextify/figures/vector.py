"""SVG and EPS conversion to PDF -- the two formats Tectonic cannot embed.

Split out of :mod:`latextify.figures.convert` (2026-08-10). Both paths follow
the same shape: try a converter, and on failure copy the source through with
an actionable warning naming the fix, so the emit never dies over one figure.
See ``convert.py``'s module docstring for the verified evidence behind each
(why cairosvg fails on Windows, what Tectonic actually reports for an EPS).

``_cairosvg_convert``/``_svglib_convert``/``_ghostscript_convert`` are thin
wrappers purely so tests can monkeypatch success and failure without
depending on what the machine running them happens to have installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from latextify.figures.crop import CROP_NOTE, apply_crop, wants_crop
from latextify.figures.outcome import ConversionOutcome
from latextify.model.figure import CropRect

#: Ghostscript executable names to probe for, in order (Windows ships
#: `gswin64c`/`gswin32c`; POSIX systems ship `gs`).
_GHOSTSCRIPT_CANDIDATES = ("gs", "gswin64c", "gswin32c")


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


def convert_svg(src: Path, dest_dir: Path, number: int, *, prefix: str = "") -> ConversionOutcome:
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


def convert_eps(src: Path, dest_dir: Path, number: int, *, prefix: str = "") -> ConversionOutcome:
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
# EMF/WMF -> PDF (optional detected converter)
# --------------------------------------------------------------------------- #

#: Converters probed for, in order. NEITHER IS A DEPENDENCY -- this mirrors the
#: Ghostscript/EPS path above exactly: if one happens to be on PATH it is used,
#: and if not the conversion degrades to an actionable warning. That is what
#: keeps the offline install kit (which bundles only pandoc + Tectonic, both
#: pip-installable) buildable; a heavyweight external app could not ride along
#: in it. Owner gate GUI_OPTIONS_FORMATS_PLAN, resolved 2026-08-10: an optional
#: DETECTED converter is in scope, a declared dependency is not.
#:
#: LibreOffice is listed first because it reads both EMF and WMF and ships on
#: most Linux distributions; Inkscape handles EMF/WMF too and is the more
#: common install on a figure-drawing workstation.
_METAFILE_CONVERTERS = ("soffice", "libreoffice", "inkscape")
_METAFILE_CONVERTER_TIMEOUT_SECONDS = 60
_MAX_METAFILE_RASTER_PIXELS = 40_000_000

_METAFILE_UNSUPPORTED_NOTE = (
    "Tectonic cannot include Windows metafiles (EMF/WMF); its xdvipdfmx PDF "
    "backend has no metafile support, so a raw \\includegraphics of one fails "
    'with "Cannot determine size of graphic".'
)


def _find_metafile_converter() -> str | None:
    for name in _METAFILE_CONVERTERS:
        found = shutil.which(name)
        if found:
            return found
    # GUI launches on Windows commonly do not inherit the installer's PATH.
    # Probe the normal application locations before falling back to raster.
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relatives = (
        Path("LibreOffice/program/soffice.exe"),
        Path("Inkscape/bin/inkscape.exe"),
        Path("Programs/Inkscape/bin/inkscape.exe"),
    )
    for root in roots:
        if not root:
            continue
        for relative in relatives:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def _pillow_metafile_convert(
    src: Path, dest: Path, *, dpi: int = 600, crop: CropRect | None = None
) -> None:
    """Rasterize a Windows metafile through Pillow's native WMF/EMF decoder."""
    from PIL import Image

    with Image.open(src) as image:
        source_dpi = image.info.get("dpi", 72)
        if isinstance(source_dpi, tuple):
            xdpi, ydpi = source_dpi
        else:
            xdpi = ydpi = source_dpi
        target_width = int(image.width * dpi / xdpi)
        target_height = int(image.height * dpi / ydpi)
        if target_width * target_height > _MAX_METAFILE_RASTER_PIXELS:
            raise OSError("rasterized metafile exceeds the 40-megapixel safety limit")
        image.load(dpi=dpi)
        if crop is not None and crop.is_effective():
            image = apply_crop(image, crop)
        image.convert("RGBA" if "A" in image.getbands() else "RGB").save(dest, format="PNG")


def _metafile_convert(binary: str, src: Path, dest: Path) -> None:
    """Thin wrapper around the converter invocation -- its own call point for
    the same monkeypatch-testability reason as ``_ghostscript_convert``.

    The two converter families take different flags and, critically, differ in
    where they put the result: Inkscape writes exactly the path it is given,
    while LibreOffice only accepts an output DIRECTORY and names the file
    itself (``<stem>.pdf``), so that one is renamed into place afterwards.
    """
    if Path(binary).stem.lower() == "inkscape":
        subprocess.run(
            [binary, "--export-type=pdf", f"--export-filename={dest}", str(src)],
            check=True,
            capture_output=True,
            text=True,
            timeout=_METAFILE_CONVERTER_TIMEOUT_SECONDS,
        )
        return

    subprocess.run(
        [binary, "--headless", "--convert-to", "pdf", "--outdir", str(dest.parent), str(src)],
        check=True,
        capture_output=True,
        text=True,
        timeout=_METAFILE_CONVERTER_TIMEOUT_SECONDS,
    )
    produced = dest.parent / f"{src.stem}.pdf"
    if produced != dest:
        if not produced.is_file():
            raise OSError(f"{binary} reported success but wrote no {produced.name}")
        produced.replace(dest)


def convert_metafile(
    src: Path,
    dest_dir: Path,
    number: int,
    *,
    prefix: str = "",
    crop: CropRect | None = None,
) -> ConversionOutcome:
    """Convert ``src`` (a .emf/.wmf) to PDF via whichever converter is present.

    Like :func:`latextify.figures.raster.convert_tiff` and unlike
    :func:`convert_eps`, a failure writes NOTHING at the destination. Copying
    the metafile through would silently reintroduce the exact compile failure
    this function exists to prevent -- and before this path existed that is
    precisely what happened: an EMF figure was copied to ``figures/fig<N>.emf``
    with no note and no warning, and the compile died on it.
    """
    dest = dest_dir / f"fig{prefix}{number}.pdf"
    binary = _find_metafile_converter()
    if binary is None:
        raster_dest = dest.with_suffix(".png")
        try:
            _pillow_metafile_convert(src, raster_dest, crop=crop)
        except Exception as exc:  # Pillow decoder failures vary; never crash the emit
            raster_dest.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)  # never preserve a previous run's vector output
            return ConversionOutcome(
                dest_path=dest,
                warning=(
                    f"{_METAFILE_UNSUPPORTED_NOTE} No LibreOffice or Inkscape converter "
                    f"was found, and Pillow could not rasterize {src.name} ({exc}); "
                    "export it as PDF/PNG, supply it via figures.yaml, or install a converter."
                ),
            )
        crop_note = f" {CROP_NOTE}" if wants_crop(crop) else ""
        return ConversionOutcome(
            dest_path=raster_dest,
            warning=(
                f"{src.name} was rasterized to PNG at 600 DPI because no vector EMF/WMF "
                "converter was found. Install LibreOffice/Inkscape or supply PDF for "
                f"vector-quality output; verify the rendered figure.{crop_note}"
            ),
        )
    try:
        _metafile_convert(binary, src, dest)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        dest.unlink(missing_ok=True)  # discard any partial/failed write
        raster_dest = dest.with_suffix(".png")
        try:
            _pillow_metafile_convert(src, raster_dest, crop=crop)
        except Exception:  # Pillow decoder failures vary; never crash the emit
            raster_dest.unlink(missing_ok=True)
        else:
            crop_note = f" {CROP_NOTE}" if wants_crop(crop) else ""
            return ConversionOutcome(
                dest_path=raster_dest,
                warning=(
                    f"{Path(binary).name} could not convert {src.name} to vector PDF "
                    f"({exc}); it was rasterized to PNG at 600 DPI instead. Verify it."
                    f"{crop_note}"
                ),
            )
        return ConversionOutcome(
            dest_path=dest,
            warning=(
                f"{_METAFILE_UNSUPPORTED_NOTE} {Path(binary).name} was found but the "
                f"conversion of {src.name} to PDF failed ({exc}); nothing was written to "
                f"figures/{dest.name}. Export the figure as PDF/PNG and supply it via "
                "figures.yaml or a folder override."
            ),
        )
    return ConversionOutcome(
        dest_path=dest, note=f"EMF/WMF converted to PDF via {Path(binary).name}."
    )
