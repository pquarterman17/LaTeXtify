"""Word image-crop (``a:srcRect``) handling -- FORMATS_AND_PRIVACY_PLAN item 2.

Word crops an image for *display* but keeps the full original pixels embedded,
so without applying the crop the hidden regions leak into the emitted figure and
the compiled PDF. These tests cover parsing the crop, associating it with the
right figure, applying it to the raster, and the vector/PDF "can't crop -> warn"
degradation.

``cropped_figure.docx`` (see ``tests/fixtures/make_cropped_figure.py``) embeds a
100x100 four-quadrant image cropped to its top-left (red) quadrant, so a correct
crop yields a 50x50 all-red PNG.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from latextify.emit.project import _copy_figures, emit_project
from latextify.figures.convert import convert_for_latex
from latextify.figures.crop import _parse_srcrect, apply_crop, attach_crops, image_crops
from latextify.figures.extract import extract_figures
from latextify.model.figure import CropRect, Figure, FigureSource

FIXTURES = Path(__file__).parent / "fixtures"
CROPPED_DOCX = FIXTURES / "cropped_figure.docx"
FIGURES_DOCX = FIXTURES / "figures.docx"


def _copy(tmp_path: Path, src: Path) -> Path:
    dest = tmp_path / src.name
    shutil.copy(src, dest)
    return dest


def _srcrect(left: str = "0", top: str = "0", right: str = "0", bottom: str = "0") -> ET.Element:
    el = ET.Element("srcRect")
    for key, value in (("l", left), ("t", top), ("r", right), ("b", bottom)):
        el.set(key, value)
    return el


def _fig(number: int, name: str) -> Figure:
    return Figure(number=number, caption="", embedded_path=Path("media") / name)


# --------------------------------------------------------------------------- #
# _parse_srcrect -- thousandths-of-a-percent insets, with clamping/guards
# --------------------------------------------------------------------------- #


def test_parse_srcrect_reads_fractions():
    assert _parse_srcrect(_srcrect(left="10000", right="25000")) == CropRect(left=0.1, right=0.25)


def test_parse_srcrect_clamps_negative_to_zero():
    # A negative inset is an outset/padding (whole image shows) -- clamp to 0 so
    # a crop never asks to reveal more than the original.
    assert _parse_srcrect(_srcrect(left="-20000", right="30000")) == CropRect(right=0.3)


def test_parse_srcrect_noop_returns_none():
    assert _parse_srcrect(_srcrect()) is None


def test_parse_srcrect_degenerate_returns_none():
    # l + r >= 100% would crop the whole width away.
    assert _parse_srcrect(_srcrect(left="60000", right="50000")) is None


def test_parse_srcrect_bad_value_is_treated_as_zero():
    assert _parse_srcrect(_srcrect(left="oops", right="25000")) == CropRect(right=0.25)


# --------------------------------------------------------------------------- #
# _apply_crop -- fractional insets -> pixel box
# --------------------------------------------------------------------------- #


def test_apply_crop_trims_to_visible_region():
    image = Image.new("RGB", (100, 80))
    out = apply_crop(image, CropRect(left=0.1, top=0.25, right=0.2, bottom=0.5))
    # left=10, top=20, right=80, bottom=40 -> 70 x 20
    assert out.size == (70, 20)


def test_apply_crop_noop_returns_same_image():
    image = Image.new("RGB", (10, 10))
    assert apply_crop(image, CropRect()) is image


# --------------------------------------------------------------------------- #
# _attach_crops -- positional (verified) primary, unique-basename fallback
# --------------------------------------------------------------------------- #


def test_attach_crops_no_crops_returns_input_unchanged():
    figs = (_fig(1, "image1.png"),)
    assert attach_crops(figs, (("image1.png", None),)) is figs


def test_attach_crops_positional_verified_by_basename():
    figs = (_fig(1, "image1.png"), _fig(2, "image2.png"))
    ordered = (("image1.png", None), ("image2.png", CropRect(right=0.5)))
    out = attach_crops(figs, ordered)
    assert out[0].crop is None
    assert out[1].crop == CropRect(right=0.5)


def test_attach_crops_same_image_two_crops_kept_distinct_positionally():
    # One media file reused with two different crops: the verified positional
    # path assigns each figure its own crop (the case basename-keying can't).
    figs = (_fig(1, "image1.png"), _fig(2, "image1.png"))
    ordered = (("image1.png", CropRect(right=0.25)), ("image1.png", CropRect(bottom=0.5)))
    out = attach_crops(figs, ordered)
    assert out[0].crop == CropRect(right=0.25)
    assert out[1].crop == CropRect(bottom=0.5)


def test_attach_crops_falls_back_to_unique_basename_on_misalignment():
    # Extra unmatched entry -> counts differ -> positional rejected; the unique
    # basename fallback still crops the one figure it can match unambiguously.
    figs = (_fig(1, "image1.png"),)
    ordered = (("image2.png", None), ("image1.png", CropRect(right=0.5)))
    out = attach_crops(figs, ordered)
    assert out[0].crop == CropRect(right=0.5)


def test_attach_crops_skips_ambiguous_basename_rather_than_guess():
    # Misaligned AND the basename is cropped two different ways -> never guess
    # which crop belongs to the figure; leave it uncropped.
    figs = (_fig(1, "image1.png"), _fig(9, "other.png"))
    ordered = (
        ("image1.png", CropRect(right=0.25)),
        ("image1.png", CropRect(bottom=0.5)),
        ("image1.png", CropRect(right=0.25)),
    )
    out = attach_crops(figs, ordered)
    assert out[0].crop is None
    assert out[1].crop is None


# --------------------------------------------------------------------------- #
# _image_crops -- reading srcRect straight from the docx zip
# --------------------------------------------------------------------------- #


def test_image_crops_reads_srcrect_from_fixture():
    assert image_crops(CROPPED_DOCX) == (("image1.png", CropRect(right=0.5, bottom=0.5)),)


def test_image_crops_empty_for_uncropped_docx():
    crops = image_crops(FIGURES_DOCX)
    assert len(crops) == 3
    assert all(crop is None for _basename, crop in crops)


def test_image_crops_missing_docx_degrades_to_empty(tmp_path):
    # Pure fallback, like _textbox_captions: an unreadable package never raises.
    assert image_crops(tmp_path / "nope.docx") == ()


# --------------------------------------------------------------------------- #
# End-to-end: extract + emit
# --------------------------------------------------------------------------- #


def test_extract_figures_attaches_crop_from_docx(tmp_path):
    docx = _copy(tmp_path, CROPPED_DOCX)
    figs = extract_figures(docx, tmp_path / "media")
    assert len(figs) == 1
    assert figs[0].crop == CropRect(right=0.5, bottom=0.5)


def test_emit_crops_embedded_image_to_its_visible_region(tmp_path):
    docx = _copy(tmp_path, CROPPED_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    fig1 = result.figures_dir / "fig1.png"
    assert fig1.is_file()
    with Image.open(fig1) as image:
        rgb = image.convert("RGB")
        # Right and bottom halves removed: 100x100 -> 50x50.
        assert rgb.size == (50, 50)
        width, height = rgb.size
        # Every corner of what remains is the red top-left quadrant -- green,
        # blue, and yellow (the cropped-out regions) are gone.
        for x, y in ((1, 1), (width - 2, 1), (1, height - 2), (width - 2, height - 2)):
            r, g, b = rgb.getpixel((x, y))
            assert r > 150 and g < 100 and b < 100, (x, y, (r, g, b))
    # The crop is recorded for the report.
    assert any("Cropped image" in (f.conversion_note or "") for f in result.figures)


def test_uncropped_figure_passes_through_full_size(tmp_path):
    docx = _copy(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    assert all(f.crop is None for f in result.figures)
    # make_figures.py builds tiny 4x4 solid PNGs; passthrough leaves them intact.
    with Image.open(result.figures_dir / "fig1.png") as image:
        assert image.size == (4, 4)


# --------------------------------------------------------------------------- #
# Guards: override not cropped; vector/PDF crop -> warning
# --------------------------------------------------------------------------- #


def test_override_figure_is_not_cropped(tmp_path):
    # The srcRect belongs to the EMBEDDED original; an override is a deliberate
    # replacement authored against no crop, so it must pass through uncropped.
    override = tmp_path / "override.png"
    Image.new("RGB", (100, 100), (10, 20, 30)).save(override)
    figure = Figure(
        number=1,
        caption="",
        embedded_path=tmp_path / "orig.png",
        override_path=override,
        source=FigureSource.OVERRIDE,
        crop=CropRect(right=0.5, bottom=0.5),
    )
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()

    _copy_figures((figure,), figures_dir)

    with Image.open(figures_dir / "fig1.png") as image:
        assert image.size == (100, 100)  # NOT cropped to 50x50


def test_crop_on_pdf_figure_warns_uncroppable(tmp_path):
    src = tmp_path / "diagram.pdf"
    src.write_bytes(b"%PDF-1.4 not a real pdf")
    outcome = convert_for_latex(src, tmp_path, 1, crop=CropRect(right=0.5))
    assert outcome.dest_path.is_file()  # still emitted (uncropped)
    assert outcome.warning is not None and "PDF" in outcome.warning


def test_crop_on_svg_figure_warns_uncroppable(tmp_path):
    src = tmp_path / "diagram.svg"
    src.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10" fill="blue"/></svg>',
        encoding="utf-8",
    )
    outcome = convert_for_latex(src, tmp_path, 1, crop=CropRect(right=0.5))
    assert outcome.warning is not None and "SVG" in outcome.warning
