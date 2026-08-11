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
    compile_document,
    find_tectonic,
)
from latextify.figures import raster as raster_mod
from latextify.figures import vector as vector_mod
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
# Alpha flattening on passthrough rasters (a transparent PNG composites against
# nothing in the PDF -> faint halo/edge lines; observed on a real manuscript's
# only RGBA figure). Root fix: flatten any alpha onto white on the way through.
# --------------------------------------------------------------------------- #


def test_has_alpha_predicate():
    from PIL import Image

    from latextify.figures.raster import _has_alpha

    assert _has_alpha(Image.new("RGBA", (2, 2))) is True
    assert _has_alpha(Image.new("LA", (2, 2))) is True
    assert _has_alpha(Image.new("RGB", (2, 2))) is False
    assert _has_alpha(Image.new("L", (2, 2))) is False
    # Palette transparency lives in an info entry, not a band.
    palette = Image.new("P", (2, 2))
    assert _has_alpha(palette) is False
    palette.info["transparency"] = 0
    assert _has_alpha(palette) is True


def test_transparent_png_is_flattened_onto_white(tmp_path):
    from PIL import Image

    src = tmp_path / "fig.png"
    # Fully transparent pixels whose *stored* RGB is red: flattening must
    # discard that red for white, proving it composites (not just drops alpha).
    Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(src)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 2)

    assert outcome.dest_path == dest_dir / "fig2.png"
    assert outcome.note == "Flattened image transparency onto a white background."
    assert outcome.warning is None
    with Image.open(outcome.dest_path) as out:
        assert out.mode == "RGB"  # alpha channel gone
        assert out.getpixel((0, 0)) == (255, 255, 255)  # transparent -> white


def test_semitransparent_png_composites_over_white(tmp_path):
    from PIL import Image

    src = tmp_path / "fig.png"
    # 50%-opacity black over white lands near mid-grey (255*(1-0.502) ~= 127),
    # not pure black or pure white -- true compositing, not a channel drop.
    Image.new("RGBA", (2, 2), (0, 0, 0, 128)).save(src)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 1)

    with Image.open(outcome.dest_path) as out:
        r, g, b = out.getpixel((0, 0))
        assert r == g == b and 120 <= r <= 135


def test_opaque_png_passes_through_byte_for_byte(tmp_path):
    from PIL import Image

    src = tmp_path / "fig.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(src)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()
    original = src.read_bytes()

    outcome = convert_for_latex(src, dest_dir, 3)

    # No alpha -> no re-encode; the bytes are copied verbatim (note stays None).
    assert outcome.note is None
    assert outcome.dest_path.read_bytes() == original


def test_unreadable_png_falls_back_to_plain_copy(tmp_path):
    # Pillow cannot open these bytes; the flatten step must swallow that and
    # leave a byte-for-byte copy rather than failing the emit.
    src = tmp_path / "fig.png"
    src.write_bytes(b"not really a png")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 4)

    assert outcome.note is None and outcome.warning is None
    assert outcome.dest_path.read_bytes() == b"not really a png"


def test_transparent_tiff_converts_to_opaque_png(tmp_path):
    from PIL import Image

    src = tmp_path / "fig.tiff"
    Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(src, format="TIFF")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 5)

    assert outcome.dest_path == dest_dir / "fig5.png"
    with Image.open(outcome.dest_path) as out:
        assert "A" not in out.getbands()  # opaque after flatten
        assert out.getpixel((0, 0)) == (255, 255, 255)


# --------------------------------------------------------------------------- #
# SVG -> PDF
# --------------------------------------------------------------------------- #


def test_svg_uses_cairosvg_when_it_succeeds(tmp_path, monkeypatch):
    def fake_cairosvg(src: Path, dest: Path) -> None:
        dest.write_bytes(b"%PDF-1.4 from cairosvg\n")

    monkeypatch.setattr(vector_mod, "_cairosvg_convert", fake_cairosvg)

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

    monkeypatch.setattr(vector_mod, "_cairosvg_convert", raise_import_error)

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

    monkeypatch.setattr(vector_mod, "_cairosvg_convert", raise_dll_oserror)

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

    monkeypatch.setattr(vector_mod, "_cairosvg_convert", raise_cairo)
    monkeypatch.setattr(vector_mod, "_svglib_convert", raise_svglib)

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
    monkeypatch.setattr(vector_mod.shutil, "which", lambda name: None)

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
    monkeypatch.setattr(vector_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_ghostscript(gs_binary: str, src: Path, dest: Path) -> None:
        dest.write_bytes(b"%PDF-1.4 from ghostscript\n")

    monkeypatch.setattr(vector_mod, "_ghostscript_convert", fake_ghostscript)

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
    monkeypatch.setattr(vector_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def failing_ghostscript(gs_binary: str, src: Path, dest: Path) -> None:
        raise subprocess.CalledProcessError(1, [gs_binary])

    monkeypatch.setattr(vector_mod, "_ghostscript_convert", failing_ghostscript)

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

    monkeypatch.setattr(vector_mod.shutil, "which", fake_which)

    assert vector_mod._find_ghostscript() == "/usr/bin/gswin64c"
    assert seen == ["gs", "gswin64c"]


# --------------------------------------------------------------------------- #
# TIFF -> PNG (Pillow)
# --------------------------------------------------------------------------- #


def _write_tiff(path: Path, color: tuple[int, int, int] = (200, 30, 30), size=(8, 8)) -> None:
    from PIL import Image

    Image.new("RGB", size, color).save(path, format="TIFF")


def _is_png(path: Path) -> bool:
    return path.is_file() and path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("ext", [".tif", ".tiff"])
def test_tiff_converts_to_png_via_pillow(tmp_path, ext):
    src = tmp_path / f"fig{ext}"
    _write_tiff(src)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 10)

    assert outcome.dest_path == dest_dir / "fig10.png"
    assert outcome.warning is None
    assert outcome.note == "TIFF converted to PNG via Pillow."
    assert _is_png(outcome.dest_path)


def test_tiff_cmyk_mode_converts_without_error(tmp_path):
    # A mode PNG cannot encode directly (CMYK is common in print-oriented
    # TIFF exports) must still convert cleanly via the RGB/RGBA normalization
    # in _pillow_convert, not raise.
    from PIL import Image

    src = tmp_path / "fig.tiff"
    Image.new("CMYK", (8, 8), (0, 0, 0, 0)).save(src, format="TIFF")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 11)

    assert outcome.warning is None
    assert _is_png(outcome.dest_path)


def test_tiff_conversion_failure_writes_nothing_and_warns(tmp_path, monkeypatch):
    # Unlike SVG/EPS, a failed TIFF conversion must NOT fall back to copying
    # the raw .tiff through -- that would silently reintroduce the exact
    # "Cannot determine size of graphic" compile failure this path exists to
    # prevent.
    def raise_conversion_error(src: Path, dest: Path) -> None:
        raise OSError("truncated TIFF file")

    monkeypatch.setattr(raster_mod, "_pillow_convert", raise_conversion_error)

    src = tmp_path / "fig.tiff"
    src.write_bytes(b"not a real tiff")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 12)

    assert outcome.note is None
    assert "Pillow" in outcome.warning
    assert "fig.tiff" in outcome.warning
    assert "Cannot determine size of graphic" in outcome.warning
    assert not outcome.dest_path.exists()
    # No raw .tif/.tiff (or anything else) was written into figures_dir at all.
    assert list(dest_dir.iterdir()) == []


def test_tiff_conversion_failure_cleans_up_partial_write(tmp_path, monkeypatch):
    def raise_after_partial_write(src: Path, dest: Path) -> None:
        dest.write_bytes(b"partial garbage")
        raise ValueError("boom")

    monkeypatch.setattr(raster_mod, "_pillow_convert", raise_after_partial_write)

    src = tmp_path / "fig.tiff"
    src.write_bytes(b"not a real tiff")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 13)

    assert not outcome.dest_path.exists()
    assert list(dest_dir.iterdir()) == []


# --------------------------------------------------------------------------- #
# Unrecognized extension: still passthrough, not a crash
# --------------------------------------------------------------------------- #


def test_unrecognized_extension_falls_back_to_passthrough(tmp_path):
    # .tiff is no longer "unrecognized" -- it gets its own TIFF->PNG
    # conversion path (see the TIFF section below) -- so this uses .bmp, a
    # real image format latextify still has no dedicated handling for.
    src = tmp_path / "fig.bmp"
    src.write_bytes(b"fake-bmp")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 9)

    assert outcome.dest_path == dest_dir / "fig9.bmp"
    assert outcome.dest_path.read_bytes() == b"fake-bmp"
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
    # Detection only -- must NOT download at collection time: anonymous
    # GitHub API calls from CI runners hit rate limits, and unit jobs
    # deselect tectonic tests anyway. ensure_tectonic() still runs (and
    # downloads if needed) inside the marked tests themselves; CI's
    # integration job pre-fetches the binary before pytest.
    return find_tectonic() is not None


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


# --------------------------------------------------------------------------- #
# EMF/WMF -> PDF (GUI_OPTIONS_FORMATS_PLAN item 11)
#
# Word's native vector format for a pasted chart or drawing. Tectonic cannot
# embed a metafile, so before this path existed an .emf figure was copied
# straight through to figures/fig<N>.emf with NO note and NO warning, and the
# compile died on it -- the same silent failure the TIFF path was built to
# prevent. The converter is optional and DETECTED (LibreOffice or Inkscape on
# PATH), never a declared dependency: the offline install kit bundles only
# pandoc + Tectonic, and a heavyweight external app could not ride along.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ext", [".emf", ".wmf"])
def test_metafile_without_a_converter_writes_nothing_and_warns(tmp_path, monkeypatch, ext):
    monkeypatch.setattr(vector_mod.shutil, "which", lambda name: None)
    src = tmp_path / f"chart{ext}"
    src.write_bytes(b"\x01\x00\x00\x00" + b"\x00" * 64)
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 3)

    # Nothing written: copying the metafile through would reintroduce exactly
    # the compile failure this path exists to prevent.
    assert not outcome.dest_path.exists()
    assert list(dest_dir.iterdir()) == []
    assert outcome.note is None
    assert "Cannot determine size of graphic" in outcome.warning
    assert "LibreOffice or Inkscape" in outcome.warning
    assert "figures.yaml" in outcome.warning  # names the fix


def test_metafile_is_not_silently_passed_through(tmp_path, monkeypatch):
    """The regression this item fixes: before it, an .emf reached the output
    tree unchanged, with no warning, and broke the compile."""
    monkeypatch.setattr(vector_mod.shutil, "which", lambda name: None)
    src = tmp_path / "chart.emf"
    src.write_bytes(b"\x01\x00\x00\x00")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 1)

    assert not (dest_dir / "fig1.emf").exists(), "a raw metafile must never reach figures/"
    assert outcome.warning is not None, "a metafile figure must never fail silently"


def test_metafile_converts_via_inkscape_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vector_mod.shutil, "which", lambda name: "/usr/bin/inkscape" if name == "inkscape" else None
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        Path(cmd[2].split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(vector_mod.subprocess, "run", fake_run)
    src = tmp_path / "chart.emf"
    src.write_bytes(b"\x01\x00\x00\x00")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 2)

    assert outcome.dest_path == dest_dir / "fig2.pdf"
    assert outcome.dest_path.read_bytes().startswith(b"%PDF")
    assert outcome.warning is None
    assert "inkscape" in outcome.note
    assert "--export-type=pdf" in calls[0]


def test_metafile_renames_libreoffice_output_into_place(tmp_path, monkeypatch):
    """LibreOffice takes an output DIRECTORY and names the file itself, so the
    result lands at <stem>.pdf and has to be moved to fig<N>.pdf."""
    monkeypatch.setattr(
        vector_mod.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None
    )

    def fake_run(cmd, **_kw):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "chart.pdf").write_bytes(b"%PDF-1.4 fake")  # LibreOffice's own naming
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(vector_mod.subprocess, "run", fake_run)
    src = tmp_path / "chart.emf"
    src.write_bytes(b"\x01\x00\x00\x00")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 5)

    assert outcome.dest_path == dest_dir / "fig5.pdf"
    assert outcome.dest_path.read_bytes().startswith(b"%PDF")
    assert not (dest_dir / "chart.pdf").exists(), "the converter's own name must not linger"
    assert outcome.warning is None


def test_metafile_converter_failure_writes_nothing_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vector_mod.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None
    )

    def failing_run(cmd, **_kw):
        raise subprocess.CalledProcessError(1, cmd, stderr="converter exploded")

    monkeypatch.setattr(vector_mod.subprocess, "run", failing_run)
    src = tmp_path / "chart.wmf"
    src.write_bytes(b"\xd7\xcd\xc6\x9a")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 4)

    assert list(dest_dir.iterdir()) == [], "a failed conversion must leave no partial file"
    assert "soffice" in outcome.warning
    assert outcome.note is None


def test_libreoffice_silent_no_output_is_reported_not_swallowed(tmp_path, monkeypatch):
    """A converter that exits 0 without producing a file must still warn."""
    monkeypatch.setattr(
        vector_mod.shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None
    )
    monkeypatch.setattr(
        vector_mod.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    src = tmp_path / "chart.emf"
    src.write_bytes(b"\x01\x00\x00\x00")
    dest_dir = tmp_path / "figures"
    dest_dir.mkdir()

    outcome = convert_for_latex(src, dest_dir, 6)

    assert outcome.warning is not None
    assert not outcome.dest_path.exists()
