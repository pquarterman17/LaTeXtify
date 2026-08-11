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

import shutil
import subprocess
from pathlib import Path

from latextify.figures.outcome import ConversionOutcome

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
    return None


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
        )
        return

    subprocess.run(
        [binary, "--headless", "--convert-to", "pdf", "--outdir", str(dest.parent), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    produced = dest.parent / f"{src.stem}.pdf"
    if produced != dest:
        if not produced.is_file():
            raise OSError(f"{binary} reported success but wrote no {produced.name}")
        produced.replace(dest)


def convert_metafile(
    src: Path, dest_dir: Path, number: int, *, prefix: str = ""
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
        return ConversionOutcome(
            dest_path=dest,
            warning=(
                f"{_METAFILE_UNSUPPORTED_NOTE} No converter (LibreOffice or Inkscape) was "
                f"found on PATH to convert {src.name}, so nothing was written to "
                f"figures/{dest.name}. Install one, or export the figure as PDF/PNG and "
                "supply it via figures.yaml or a folder override."
            ),
        )
    try:
        _metafile_convert(binary, src, dest)
    except (subprocess.CalledProcessError, OSError) as exc:
        dest.unlink(missing_ok=True)  # discard any partial/failed write
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
