"""Tests for latextify.figures.convert (plan item 15).

Covers the three format families:
    * PDF/PNG/JPG -- passthrough, unconditional
    * SVG -- cairosvg first, svglib+reportlab fallback on ImportError/OSError
      (the latter VERIFIED as the actual failure mode on this project's
      Windows dev machine: cairosvg imports fine but its ``svg2pdf`` call
      raises OSError because no ``libcairo-2.dll`` is installed)
    * EPS -- Ghostscript when found on PATH, else an actionable warning
      (Tectonic itself is proven, in ``TestTectonicRejectsEps`` below, to
      reject raw EPS ``\\includegraphics`` outright)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from latextify.compile.tectonic import (
    TectonicNotAvailableError,
    compile_document,
    ensure_tectonic,
)
from latextify.figures import convert as convert_mod
from latextify.figures.convert import convert_for_latex

_MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    '<rect width="100" height="100" fill="red"/></svg>'
)

_MINIMAL_EPS = (
    "%!PS-Adobe-3.0 EPSF-3.0\n"
    "%%BoundingBox: 0 0 100 100\n"
    "%%HiResBoundingBox: 0 0 100.0 100.0\n"
    "%%Creator: latextify test\n"
    "%%EndComments\n"
    "newpath\n10 10 moveto\n90 10 lineto\n90 90 lineto\n10 90 lineto\n"
    "closepath\n0.5 setgray\nfill\n%%EOF\n"
)


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.read_bytes().startswith(b"%PDF-")


# --------------------------------------------------------------------------- #
# Passthrough: PDF / PNG / JPG / JPEG
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ext,content",
    [
        (".pdf", b"%PDF-1.4 fake\n"),
        (".png", b"fake-png"),
        (".jpg", b"fake-jpg"),
        (".jpeg", b"fake-jpeg"),
    ],
)
def test_passthrough_formats_are_copied_unchanged(tmp_path, ext, content):
    src = tmp_path / f"source{ext}"
    src.write_bytes(content)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 7)

    assert outcome.dest_path == dest_dir / f"fig7{ext}"
    assert outcome.dest_path.read_bytes() == content
    assert outcome.note is None
    assert outcome.warning is None


# --------------------------------------------------------------------------- #
# SVG -> PDF
# --------------------------------------------------------------------------- #


def test_svg_uses_cairosvg_when_it_succeeds(tmp_path, monkeypatch):
    def fake_cairosvg(src: Path, dest: Path) -> None:
        dest.write_bytes(b"%PDF-1.4 from cairosvg\n")

    monkeypatch.setattr(convert_mod, "_cairosvg_convert", fake_cairosvg)

    src = tmp_path / "fig.svg"
    src.write_text(_MINIMAL_SVG, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 1)

    assert outcome.dest_path == dest_dir / "fig1.pdf"
    assert outcome.warning is None
    assert outcome.note == "SVG converted to PDF via cairosvg."
    assert _is_pdf(outcome.dest_path)


def test_svg_falls_back_to_svglib_on_cairosvg_import_error(tmp_path, monkeypatch):
    def raise_import_error(src: Path, dest: Path) -> None:
        raise ImportError("cairosvg not installed")

    monkeypatch.setattr(convert_mod, "_cairosvg_convert", raise_import_error)

    src = tmp_path / "fig.svg"
    src.write_text(_MINIMAL_SVG, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 2)

    assert outcome.dest_path == dest_dir / "fig2.pdf"
    assert outcome.warning is None
    assert "svglib+reportlab" in outcome.note
    assert "fidelity" in outcome.note.lower()
    assert _is_pdf(outcome.dest_path)


def test_svg_falls_back_to_svglib_on_cairosvg_dll_failure(tmp_path, monkeypatch):
    # VERIFIED (2026-07-11): this is the *actual* failure mode on this
    # project's Windows dev machine -- cairosvg imports without error, but
    # svg2pdf() raises OSError at call time because libcairo-2.dll is not
    # installed (cairosvg is a ctypes binding, not a self-contained wheel).
    def raise_dll_oserror(src: Path, dest: Path) -> None:
        raise OSError('no library called "cairo-2" was found')

    monkeypatch.setattr(convert_mod, "_cairosvg_convert", raise_dll_oserror)

    src = tmp_path / "fig.svg"
    src.write_text(_MINIMAL_SVG, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 3)

    assert outcome.dest_path == dest_dir / "fig3.pdf"
    assert outcome.warning is None
    assert "cairo-2" in outcome.note
    assert _is_pdf(outcome.dest_path)


def test_svg_real_conversion_end_to_end_on_this_machine(tmp_path):
    # No monkeypatching: exercises whatever converter actually works on the
    # machine running the test. On this project's Windows dev machine that
    # is empirically the svglib+reportlab fallback (see the OSError test
    # above and the item 15 executor report) -- either way, the result must
    # be a real PDF landing in the output tree.
    src = tmp_path / "fig.svg"
    src.write_text(_MINIMAL_SVG, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 1)

    assert outcome.dest_path == dest_dir / "fig1.pdf"
    assert outcome.warning is None
    assert outcome.note is not None
    assert _is_pdf(outcome.dest_path)


def test_svg_double_failure_falls_back_to_passthrough_with_warning(tmp_path, monkeypatch):
    def raise_cairo(src: Path, dest: Path) -> None:
        raise OSError("no cairo")

    def raise_svglib(src: Path, dest: Path) -> None:
        raise ValueError("malformed svg")

    monkeypatch.setattr(convert_mod, "_cairosvg_convert", raise_cairo)
    monkeypatch.setattr(convert_mod, "_svglib_convert", raise_svglib)

    src = tmp_path / "fig.svg"
    src.write_text(_MINIMAL_SVG, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 4)

    assert outcome.dest_path == dest_dir / "fig4.svg"
    assert outcome.note is None
    assert "cairosvg" in outcome.warning
    assert "svglib+reportlab" in outcome.warning
    assert outcome.dest_path.read_text(encoding="utf-8") == _MINIMAL_SVG


# --------------------------------------------------------------------------- #
# EPS -> PDF (Ghostscript) / actionable warning
# --------------------------------------------------------------------------- #


def test_eps_passes_through_with_actionable_warning_when_ghostscript_absent(tmp_path, monkeypatch):
    # Real (non-monkeypatched at the `which` level) on this dev machine --
    # VERIFIED no gs/gswin64c/gswin32c is on PATH here, so this also proves
    # the fallback path fires for real, not just under a mocked absence.
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)

    src = tmp_path / "fig.eps"
    src.write_text(_MINIMAL_EPS, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 5)

    assert outcome.dest_path == dest_dir / "fig5.eps"
    assert outcome.note is None
    assert "Ghostscript" in outcome.warning
    assert "PostScript images are not supported by Tectonic" in outcome.warning
    assert outcome.dest_path.read_text(encoding="utf-8") == _MINIMAL_EPS


def test_eps_converts_via_ghostscript_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_ghostscript(gs_binary: str, src: Path, dest: Path) -> None:
        dest.write_bytes(b"%PDF-1.4 from ghostscript\n")

    monkeypatch.setattr(convert_mod, "_ghostscript_convert", fake_ghostscript)

    src = tmp_path / "fig.eps"
    src.write_text(_MINIMAL_EPS, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 6)

    assert outcome.dest_path == dest_dir / "fig6.pdf"
    assert outcome.warning is None
    assert outcome.note == "EPS converted to PDF via Ghostscript."
    assert _is_pdf(outcome.dest_path)


def test_eps_ghostscript_failure_falls_back_to_passthrough_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def failing_ghostscript(gs_binary: str, src: Path, dest: Path) -> None:
        raise subprocess.CalledProcessError(1, [gs_binary])

    monkeypatch.setattr(convert_mod, "_ghostscript_convert", failing_ghostscript)

    src = tmp_path / "fig.eps"
    src.write_text(_MINIMAL_EPS, encoding="utf-8")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 8)

    assert outcome.dest_path == dest_dir / "fig8.eps"
    assert outcome.note is None
    assert "Ghostscript was found but the conversion" in outcome.warning


def test_find_ghostscript_probes_candidate_names_in_order(monkeypatch):
    seen: list[str] = []

    def fake_which(name: str) -> str | None:
        seen.append(name)
        return "/usr/bin/gswin64c" if name == "gswin64c" else None

    monkeypatch.setattr(convert_mod.shutil, "which", fake_which)

    assert convert_mod._find_ghostscript() == "/usr/bin/gswin64c"
    assert seen == ["gs", "gswin64c"]


# --------------------------------------------------------------------------- #
# Unrecognized extension: still passthrough, not a crash
# --------------------------------------------------------------------------- #


def test_unrecognized_extension_falls_back_to_passthrough(tmp_path):
    src = tmp_path / "fig.tiff"
    src.write_bytes(b"fake-tiff")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 9)

    assert outcome.dest_path == dest_dir / "fig9.tiff"
    assert outcome.dest_path.read_bytes() == b"fake-tiff"
    assert outcome.note is None
    assert outcome.warning is None


# --------------------------------------------------------------------------- #
# Empirical: does Tectonic compile \includegraphics of a raw .eps? (item 15)
# --------------------------------------------------------------------------- #
#
# VERIFIED (2026-07-11): NO. Tectonic's xdvipdfmx PDF backend has no
# PostScript support at all -- the compile fails with:
#
#   warning: sorry, PostScript images are not supported by Tectonic
#   error: pdf: image inclusion failed for "fig.eps" (page=1).
#
# This is the plan's literal "TEST this" instruction; it settles which
# behavior latextify.figures.convert implements (Ghostscript conversion /
# actionable warning, never bare EPS passthrough).


def _tectonic_available() -> bool:
    try:
        ensure_tectonic()
        return True
    except TectonicNotAvailableError:
        return False


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_tectonic_rejects_raw_eps_includegraphics(tmp_path):
    (tmp_path / "fig.eps").write_text(_MINIMAL_EPS, encoding="utf-8")
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "Hello EPS test.\n"
        "\\includegraphics{fig.eps}\n"
        "\\end{document}\n"
    )

    result = compile_document(tex_path)

    assert not result.success
    assert "PostScript images are not supported by Tectonic" in result.raw_log
