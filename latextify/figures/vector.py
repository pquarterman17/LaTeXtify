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
