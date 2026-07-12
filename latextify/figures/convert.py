"""SVG->PDF conversion and EPS handling for LaTeX inclusion (plan item 15).

Tectonic (see ``latextify.compile.tectonic``) is built on a XeTeX-derived
engine whose ``xdvipdfmx`` PDF backend embeds PDF/PNG/JPEG directly but has
no PostScript image support at all. Two of the five formats
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
from dataclasses import dataclass
from pathlib import Path

#: Extensions always converted to PDF before inclusion.
SVG_EXTENSIONS = frozenset({".svg"})
EPS_EXTENSIONS = frozenset({".eps"})
#: Extensions Tectonic embeds natively -- copied through unchanged.
PASSTHROUGH_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

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
    src: Path, dest_dir: Path, number: int, *, prefix: str = ""
) -> ConversionOutcome:
    """Prepare figure ``number``'s resolved file ``src`` for inclusion in ``dest_dir``.

    Dispatches purely on ``src``'s extension: SVG is always converted to
    PDF, EPS is converted via Ghostscript when available (else passed
    through with a warning), and everything else (PDF/PNG/JPG/JPEG, or any
    other extension the override tiers happened to resolve to) is copied
    through unchanged as ``fig<prefix><number><ext>``.

    ``prefix`` (plan item 21) defaults to ``""``; a supplementary document's
    figures pass ``prefix="S"`` so they land as ``figS<number>.<ext>`` in the
    shared ``figures/`` directory, never colliding with the main document's
    ``fig<number>.<ext>``.
    """
    ext = src.suffix.lower()
    if ext in SVG_EXTENSIONS:
        return _convert_svg(src, dest_dir, number, prefix=prefix)
    if ext in EPS_EXTENSIONS:
        return _convert_eps(src, dest_dir, number, prefix=prefix)
    dest = dest_dir / f"fig{prefix}{number}{ext}"
    shutil.copy2(src, dest)
    return ConversionOutcome(dest_path=dest)


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
