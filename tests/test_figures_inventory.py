"""Describing what a figure actually is: vector, raster, or a raster in a PDF.

The override mechanism these tests serve has always worked; what was missing
was any way to see the result. Three failures motivated this module and each
has a test here:

    1. Figure overrides are keyed by NUMBER (document order), and nothing
       reported the numbering back, so naming ``fig3.pdf`` was a guess.
    2. A screenshot printed to PDF passes every extension check and still
       prints as pixels, so "I supplied a PDF" did not mean "I supplied
       vector art".
    3. The emitter's wide-float check measured with Pillow only, so a PDF --
       the format the override order PREFERS -- could not be measured and
       every vector figure silently lost its two-column float.

Fixtures are built at run time with Pillow and reportlab; nothing is committed
and nothing comes from a real manuscript.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from latextify.figures.inventory import (
    MIN_PRINT_DPI,
    SINGLE_COLUMN_INCHES,
    FigureKind,
    classify,
    describe,
    inventory,
    is_wide,
    raster_warnings,
)
from latextify.model.figure import Figure, FigureSource


def _png(path: Path, size: tuple[int, int] = (1200, 800)) -> Path:
    Image.new("RGB", size, (40, 90, 160)).save(path)
    return path


def _vector_pdf(path: Path, size: tuple[float, float] = (400, 200)) -> Path:
    """A PDF that is genuinely drawn: axis text plus many path operators."""
    c = canvas.Canvas(str(path), pagesize=size)
    c.drawString(20, size[1] / 2, "Wavelength (nm)")
    for i in range(400):
        c.line(i % int(size[0]), 0, i % int(size[0]), 40)
    c.save()
    return path


def _screenshot_pdf(path: Path, size: tuple[float, float] = (400, 300)) -> Path:
    """A PDF holding one image and no text -- a screenshot printed to PDF."""
    image = _png(path.with_suffix(".src.png"), (640, 480))
    c = canvas.Canvas(str(path), pagesize=size)
    c.drawImage(str(image), 0, 0, width=size[0], height=size[1])
    c.save()
    return path


def _figure(number: int, path: Path, *, source: FigureSource = FigureSource.EMBEDDED) -> Figure:
    override = path if source is not FigureSource.EMBEDDED else None
    return Figure(
        number=number,
        caption=f"Figure {number} caption.",
        embedded_path=path,
        override_path=override,
        source=source,
    )


# --------------------------------------------------------------------------- #
# classify: what the file is
# --------------------------------------------------------------------------- #


def test_raster_is_classified_and_measured(tmp_path):
    kind, measurement = classify(_png(tmp_path / "shot.png", (1200, 800)))
    assert kind is FigureKind.RASTER
    assert (measurement.width, measurement.height) == (1200.0, 800.0)
    assert measurement.pixel_width == 1200


def test_drawn_pdf_is_vector_and_reports_no_resolution(tmp_path):
    kind, measurement = classify(_vector_pdf(tmp_path / "plot.pdf"))
    assert kind is FigureKind.VECTOR
    # Measured in points, with no pixel width: vector art has no DPI to give.
    assert (measurement.width, measurement.height) == (400.0, 200.0)
    assert measurement.pixel_width is None


def test_screenshot_printed_to_pdf_is_not_mistaken_for_vector(tmp_path):
    """The failure this whole feature exists to catch: a PDF that is pixels.

    It has a vector file's extension, wins the override order over any raster,
    and still prints soft. It must be reported as what it is.
    """
    kind, measurement = classify(_screenshot_pdf(tmp_path / "shot.pdf"))
    assert kind is FigureKind.RASTER_IN_PDF
    # Carries the EMBEDDED image's pixel width, so a real DPI can be reported.
    assert measurement.pixel_width == 640


def test_vector_art_with_a_raster_inset_stays_vector(tmp_path):
    """The false positive to avoid: a plot with a micrograph inset and labels.

    Flagging this would tell an author to replace a figure that is already
    vector, so the raster-in-PDF verdict requires image data AND no text AND a
    content stream short enough to be a bare image placement.
    """
    path = tmp_path / "inset.pdf"
    inset = _png(tmp_path / "inset-src.png", (200, 150))
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.drawImage(str(inset), 10, 10, width=100, height=75)
    c.drawString(40, 250, "Figure with an inset")
    for i in range(300):
        c.line(i % 400, 0, i % 400, 20)
    c.save()

    assert classify(path)[0] is FigureKind.VECTOR


def test_unreadable_and_unknown_files_degrade_rather_than_raise(tmp_path):
    """Describing a figure must never be able to fail a conversion."""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image at all")
    assert classify(corrupt) == (FigureKind.UNKNOWN, None)

    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-1.4 and then nothing")
    assert classify(truncated)[0] is FigureKind.UNKNOWN

    assert classify(tmp_path / "does-not-exist.png") == (FigureKind.UNKNOWN, None)
    assert classify(tmp_path / "notes.txt") == (FigureKind.UNKNOWN, None)


def test_eps_and_svg_are_vector_without_being_measured(tmp_path):
    for name in ("art.eps", "art.svg", "chart.emf", "chart.wmf"):
        path = tmp_path / name
        path.write_bytes(b"")
        assert classify(path) == (FigureKind.VECTOR, None), name


# --------------------------------------------------------------------------- #
# is_wide: the emitter's two-column float decision, PDFs included
# --------------------------------------------------------------------------- #


def test_landscape_pdf_is_wide(tmp_path):
    """The regression this module fixes.

    Measuring with Pillow alone returned False for every PDF, so a landscape
    vector figure was squeezed into one column of a two-column journal.
    """
    assert is_wide(_vector_pdf(tmp_path / "wide.pdf", (600, 200))) is True


def test_portrait_pdf_is_not_wide(tmp_path):
    assert is_wide(_vector_pdf(tmp_path / "tall.pdf", (200, 600))) is False


def test_landscape_raster_is_wide_and_portrait_is_not(tmp_path):
    assert is_wide(_png(tmp_path / "wide.png", (1600, 600))) is True
    assert is_wide(_png(tmp_path / "tall.png", (600, 1600))) is False


def test_unmeasurable_figure_is_not_wide(tmp_path):
    """Sizing must degrade to single-column, never raise."""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 truncated")
    assert is_wide(bad) is False


def test_emitter_and_inventory_share_one_measurement():
    """The emitter's wide check IS this module's, so they cannot drift apart."""
    from latextify.emit.figures_copy import _is_wide_figure

    assert _is_wide_figure is is_wide


# --------------------------------------------------------------------------- #
# describe: effective print resolution
# --------------------------------------------------------------------------- #


def test_dpi_is_reported_against_the_single_column_width(tmp_path):
    facts = describe(_figure(1, _png(tmp_path / "fig.png", (1020, 1020))))
    assert facts.print_width_inches == SINGLE_COLUMN_INCHES
    assert facts.dpi == pytest.approx(1020 / SINGLE_COLUMN_INCHES)


def test_a_wide_figure_reports_dpi_against_the_wider_measure(tmp_path):
    """A figure spanning both columns is printed larger, so it needs more pixels."""
    narrow = describe(_figure(1, _png(tmp_path / "square.png", (1200, 1200))))
    wide = describe(_figure(2, _png(tmp_path / "wide.png", (1200, 400))))
    assert wide.wide and not narrow.wide
    assert wide.dpi < narrow.dpi


def test_vector_figure_reports_no_dpi_and_needs_nothing(tmp_path):
    facts = describe(_figure(1, _vector_pdf(tmp_path / "plot.pdf")))
    assert facts.is_vector
    assert facts.dpi is None
    assert facts.needs_attention is False


def test_high_resolution_raster_is_left_alone(tmp_path):
    """A micrograph at print resolution is a legitimate raster, not a defect."""
    facts = describe(_figure(1, _png(tmp_path / "micrograph.png", (2000, 2000))))
    assert facts.dpi > MIN_PRINT_DPI
    assert facts.needs_attention is False


def test_low_resolution_raster_needs_attention(tmp_path):
    facts = describe(_figure(1, _png(tmp_path / "shot.png", (400, 300))))
    assert facts.kind is FigureKind.RASTER
    assert facts.needs_attention is True


def test_unreadable_figure_is_never_flagged(tmp_path):
    """Warning about a file we failed to read would be guessing."""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"nope")
    assert describe(_figure(1, corrupt)).needs_attention is False


def test_describe_can_be_pointed_at_the_written_file(tmp_path):
    """The emitter describes its OUTPUT, not the source.

    An SVG source has become a PDF by the time figures are reported; describing
    the source would tell an author to replace an already-vector figure.
    """
    source = tmp_path / "source.svg"
    source.write_bytes(b"")
    written = _vector_pdf(tmp_path / "fig1.pdf")
    facts = describe(_figure(1, source), path=written)
    assert facts.path == written
    assert facts.measurement is not None  # the SVG itself is never measured


# --------------------------------------------------------------------------- #
# inventory + warnings
# --------------------------------------------------------------------------- #


def test_inventory_is_ordered_by_figure_number(tmp_path):
    png = _png(tmp_path / "fig.png")
    facts = inventory((_figure(3, png), _figure(1, png), _figure(2, png)))
    assert [f.number for f in facts] == [1, 2, 3]


def test_warnings_name_the_file_to_create(tmp_path):
    """The remedy must be an exact filename: not knowing which number a plot
    corresponds to is the entire difficulty this feature addresses."""
    facts = inventory((_figure(2, _png(tmp_path / "shot.png", (400, 300))),))
    (message,) = raster_warnings(facts)
    assert "figures/fig2.pdf" in message
    assert "figure 2" in message
    assert str(MIN_PRINT_DPI) in message


def test_supplement_warnings_use_the_s_prefix(tmp_path):
    facts = inventory((_figure(1, _png(tmp_path / "shot.png", (400, 300))),))
    (message,) = raster_warnings(facts, prefix="S")
    assert "figures/figS1.pdf" in message


def test_a_wrapped_screenshot_says_so_rather_than_calling_itself_a_raster(tmp_path):
    facts = inventory((_figure(1, _screenshot_pdf(tmp_path / "shot.pdf")),))
    (message,) = raster_warnings(facts)
    assert "wrapped in a PDF" in message


def test_only_figures_needing_attention_are_warned_about(tmp_path):
    facts = inventory(
        (
            _figure(1, _vector_pdf(tmp_path / "plot.pdf"), source=FigureSource.OVERRIDE),
            _figure(2, _png(tmp_path / "shot.png", (400, 300))),
            _figure(3, _png(tmp_path / "big.png", (2400, 2400))),
        )
    )
    messages = raster_warnings(facts)
    assert len(messages) == 1
    assert "figure 2" in messages[0]


def test_print_dpi_threshold_matches_the_number_quoted_in_the_cli_help():
    """`latextify figures` states 300 DPI in prose; pin the two together."""
    from latextify.cli_figures import figures_cmd

    assert MIN_PRINT_DPI == 300
    assert f"{MIN_PRINT_DPI} DPI" in figures_cmd.__doc__
