"""Supplying figures from anywhere, and repairing a caption with no image.

Stage 2 of the vector-figures plan. Three ways to hand over replacement files
without copying them next to the manuscript, and the repair that needs them:

    --figures-dir PATH    the fig<N> convention, folder anywhere
    --figure N=PATH       one file, named outright
    --figures-pdf FILE    page N replaces figure N

The caption-gap repair is the reason the tiers matter. VERIFIED on the fixture
below: a manuscript captioning figures 1-4 with no image pasted for 3 emits the
image belonging to caption 4 carrying **caption 3's text**, and leaves the
orphaned "Figure 3" paragraph in the body as ordinary prose. Given a file for
the gap, the figures are renumbered to the numbers the captions state and the
file is planted where the orphan sat -- which repairs the mis-binding as well
as filling the hole.

Fixtures are built at run time with python-docx, Pillow and reportlab.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image
from reportlab.pdfgen import canvas
from typer.testing import CliRunner

from latextify.cli import app
from latextify.emit.project import emit_project
from latextify.figures.override import (
    OverrideSources,
    build_sources,
    parse_figure_argument,
    split_figures_pdf,
)

runner = CliRunner()


def _png(path: Path, size: tuple[int, int] = (400, 300)) -> Path:
    Image.new("RGB", size, (60, 100, 170)).save(path)
    return path


def _vector_pdf(path: Path, label: str = "drawn", pages: int = 1) -> Path:
    c = canvas.Canvas(str(path), pagesize=(400, 200))
    for page in range(pages):
        c.drawString(20, 100, f"{label} {page + 1}")
        for i in range(300):
            c.line(i % 400, 0, i % 400, 40)
        c.showPage()
    c.save()
    return path


def _manuscript(path: Path, *, captions: int = 3, missing: set[int] | None = None) -> Path:
    """Captions 1..captions, omitting the pasted image for `missing`."""
    missing = missing or set()
    document = Document()
    document.add_heading("Stage 2 Fixture", level=0)
    document.add_heading("Results", level=1)
    for number in range(1, captions + 1):
        document.add_paragraph(f"Discussion of result {number}.")
        if number not in missing:
            document.add_picture(str(_png(path.parent / f"src{number}.png")), width=Inches(3))
        document.add_paragraph(f"Figure {number}: Caption {number}.", style="Caption")
    document.save(path)
    return path


def _emitted(out_dir: Path) -> str:
    return (out_dir / "revtex4-2" / "generated" / "body.tex").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# --figure N=PATH
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "argument, problem",
    [
        ("3", "NUMBER=PATH"),
        ("3=", "NUMBER=PATH"),
        ("x=file.pdf", "positive integer"),
        ("0=file.pdf", "positive integer"),
        ("-1=file.pdf", "positive integer"),
    ],
)
def test_a_malformed_figure_argument_is_rejected_by_name(argument, problem):
    """A typo must fail loudly before a conversion runs, not be ignored."""
    with pytest.raises(ValueError) as excinfo:
        parse_figure_argument(argument)
    assert problem in str(excinfo.value)


def test_a_figure_argument_naming_a_missing_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no such file"):
        parse_figure_argument(f"3={tmp_path / 'absent.pdf'}")


def test_an_explicit_figure_actually_replaces_what_ships(tmp_path):
    """The regression this caught: Figure.resolved_path listed the override
    tiers by name, so a brand-new tier relabelled provenance and shipped the
    embedded image anyway."""
    docx = _manuscript(tmp_path / "paper.docx")
    replacement = _vector_pdf(tmp_path / "spectra.pdf")

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "-j",
            "revtex4-2",
            "-o",
            str(tmp_path / "out"),
            "--figure",
            f"2={replacement}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "figures/fig2.pdf" in _emitted(tmp_path / "out")
    assert (tmp_path / "out" / "revtex4-2" / "figures" / "fig2.pdf").is_file()


def test_an_explicit_figure_beats_a_folder_for_the_same_number(tmp_path):
    docx = _manuscript(tmp_path / "paper.docx")
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    _png(folder / "fig1.png", (1000, 700))
    explicit = _vector_pdf(tmp_path / "explicit.pdf")

    result = runner.invoke(
        app,
        ["figures", str(docx), "--figures-dir", str(folder), "--figure", f"1={explicit}", "--json"],
    )

    assert result.exit_code == 0, result.output
    figure = json.loads(result.output)["figures"][0]
    assert figure["source"] == "explicit"
    assert figure["is_vector"] is True


# --------------------------------------------------------------------------- #
# --figures-dir
# --------------------------------------------------------------------------- #


def test_a_folder_anywhere_supplies_figures_by_number(tmp_path):
    """The gap this closes: files previously had to sit beside the .docx."""
    docx = _manuscript(tmp_path / "paper.docx")
    folder = tmp_path / "somewhere" / "else"
    folder.mkdir(parents=True)
    _vector_pdf(folder / "fig2.pdf")

    result = runner.invoke(app, ["figures", str(docx), "--figures-dir", str(folder), "--json"])

    assert result.exit_code == 0, result.output
    sources = [f["source"] for f in json.loads(result.output)["figures"]]
    assert sources == ["embedded", "override", "embedded"]


def test_a_folder_beats_the_one_beside_the_manuscript(tmp_path):
    """An explicitly-pointed-at folder is a deliberate choice; the beside-the-
    docx convention is a default, so the explicit one wins."""
    docx = _manuscript(tmp_path / "paper.docx")
    (tmp_path / "figures").mkdir()
    _png(tmp_path / "figures" / "fig1.png", (500, 400))
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    _vector_pdf(chosen / "fig1.pdf")

    result = runner.invoke(app, ["figures", str(docx), "--figures-dir", str(chosen), "--json"])

    assert json.loads(result.output)["figures"][0]["is_vector"] is True


def test_a_missing_folder_is_a_clean_error(tmp_path):
    docx = _manuscript(tmp_path / "paper.docx")
    result = runner.invoke(app, ["figures", str(docx), "--figures-dir", str(tmp_path / "nope")])
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# --figures-pdf
# --------------------------------------------------------------------------- #


def test_each_page_of_a_bundle_becomes_the_figure_of_that_number(tmp_path):
    written = split_figures_pdf(_vector_pdf(tmp_path / "all.pdf", pages=3), tmp_path / "split")
    assert [p.name for p in written] == ["fig1.pdf", "fig2.pdf", "fig3.pdf"]
    assert all(p.is_file() for p in written)


def test_a_bundle_supplies_every_figure_in_one_option(tmp_path):
    docx = _manuscript(tmp_path / "paper.docx")
    bundle = _vector_pdf(tmp_path / "all.pdf", pages=3)

    result = runner.invoke(app, ["figures", str(docx), "--figures-pdf", str(bundle), "--json"])

    assert result.exit_code == 0, result.output
    figures = json.loads(result.output)["figures"]
    assert all(f["is_vector"] for f in figures)
    assert all(f["source"] == "override" for f in figures)


def test_the_split_staging_directory_does_not_outlive_the_command(tmp_path):
    """The pages are staged in a temporary directory, not the user's tree."""
    docx = _manuscript(tmp_path / "paper.docx")
    bundle = _vector_pdf(tmp_path / "all.pdf", pages=3)
    before = sorted(p.name for p in tmp_path.iterdir())

    assert runner.invoke(app, ["figures", str(docx), "--figures-pdf", str(bundle)]).exit_code == 0

    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_bundle_with_fewer_pages_than_figures_supplies_what_it_has(tmp_path):
    docx = _manuscript(tmp_path / "paper.docx", captions=3)
    bundle = _vector_pdf(tmp_path / "two.pdf", pages=2)

    result = runner.invoke(app, ["figures", str(docx), "--figures-pdf", str(bundle), "--json"])

    sources = [f["source"] for f in json.loads(result.output)["figures"]]
    assert sources == ["override", "override", "embedded"]


def test_an_unreadable_bundle_is_a_clean_error(tmp_path):
    docx = _manuscript(tmp_path / "paper.docx")
    bogus = tmp_path / "not.pdf"
    bogus.write_bytes(b"certainly not a pdf")

    result = runner.invoke(app, ["figures", str(docx), "--figures-pdf", str(bogus)])

    assert result.exit_code == 1
    assert "could not be read as a PDF" in result.output


# --------------------------------------------------------------------------- #
# Caption-gap repair
# --------------------------------------------------------------------------- #


def test_without_a_replacement_the_gap_is_only_warned_about(tmp_path):
    """Unchanged behaviour: repairing the numbering when we cannot also supply
    the image would reshape a project the author is already working with."""
    docx = _manuscript(tmp_path / "gap.docx", captions=4, missing={3})

    result = emit_project(docx, "revtex4-2", tmp_path / "out")

    assert len(result.figures) == 3
    assert any("captions Figure 3" in w.message for w in result.warnings)
    # The documented mis-binding is still present, and still reported.
    assert result.figures[2].caption == "Caption 4."


def test_a_supplied_gap_is_planted_at_its_caption(tmp_path):
    docx = _manuscript(tmp_path / "gap.docx", captions=4, missing={3})
    (tmp_path / "figures").mkdir()
    _vector_pdf(tmp_path / "figures" / "fig3.pdf")

    result = emit_project(docx, "revtex4-2", tmp_path / "out")
    body = _emitted(tmp_path / "out")

    assert len(result.figures) == 4
    assert "figures/fig3.pdf" in body
    # The orphaned caption paragraph is consumed, not left as stray prose.
    assert "Figure 3: Caption 3." not in body


def test_filling_a_gap_repairs_the_shifted_captions(tmp_path):
    """The real damage a gap does: every later figure carries the wrong caption.

    Renumbering to the stated captions re-pairs each image with its own text,
    and because LaTeX numbers floats by position the printed numbering comes
    out right too.
    """
    docx = _manuscript(tmp_path / "gap.docx", captions=4, missing={3})
    (tmp_path / "figures").mkdir()
    _vector_pdf(tmp_path / "figures" / "fig3.pdf")

    result = emit_project(docx, "revtex4-2", tmp_path / "out")

    captions = {figure.number: figure.caption for figure in result.figures}
    assert captions == {
        1: "Caption 1.",
        2: "Caption 2.",
        3: "Caption 3.",
        4: "Caption 4.",
    }


def test_the_supplied_file_is_not_also_consumed_by_another_figure(tmp_path):
    """The bug found while building this: resolving overrides BEFORE
    renumbering meant a file supplied for gap 3 was also picked up by whichever
    document-order figure happened to be numbered 3 -- shipped twice, under two
    numbers, and the real figure 4 lost its own image."""
    docx = _manuscript(tmp_path / "gap.docx", captions=4, missing={3})
    (tmp_path / "figures").mkdir()
    _vector_pdf(tmp_path / "figures" / "fig3.pdf")

    emit_project(docx, "revtex4-2", tmp_path / "out")

    written = sorted(p.name for p in (tmp_path / "out" / "revtex4-2" / "figures").iterdir())
    assert written == ["fig1.png", "fig2.png", "fig3.pdf", "fig4.png"]


def test_a_gap_can_be_filled_by_any_of_the_new_sources(tmp_path):
    docx = _manuscript(tmp_path / "gap.docx", captions=4, missing={3})
    replacement = _vector_pdf(tmp_path / "missing.pdf")

    with build_sources(figure_arguments=[f"3={replacement}"]) as sources:
        result = emit_project(docx, "revtex4-2", tmp_path / "out", figure_sources=sources)

    assert len(result.figures) == 4
    assert result.figures[2].source.value == "explicit"


def test_a_partially_filled_gap_set_still_warns_about_the_rest(tmp_path):
    docx = _manuscript(tmp_path / "gap.docx", captions=5, missing={2, 4})
    replacement = _vector_pdf(tmp_path / "two.pdf")

    with build_sources(figure_arguments=[f"2={replacement}"]) as sources:
        result = emit_project(docx, "revtex4-2", tmp_path / "out", figure_sources=sources)

    messages = " ".join(w.message for w in result.warnings)
    assert "captions Figure 4" in messages
    assert "captions Figure 2" not in messages


def test_a_manuscript_with_no_gaps_is_untouched_by_the_planner(tmp_path):
    """The planner must be inert on the overwhelmingly common case."""
    docx = _manuscript(tmp_path / "fine.docx", captions=3)
    replacement = _vector_pdf(tmp_path / "extra.pdf")

    with build_sources(figure_arguments=[f"2={replacement}"]) as sources:
        result = emit_project(docx, "revtex4-2", tmp_path / "out", figure_sources=sources)

    assert [f.number for f in result.figures] == [1, 2, 3]
    assert not any("renumbered" in w.message for w in result.warnings)


def test_empty_sources_leave_resolution_exactly_as_it_was(tmp_path):
    """Stage 2 must be additive: nothing supplied means the old two tiers."""
    docx = _manuscript(tmp_path / "paper.docx")
    (tmp_path / "figures").mkdir()
    _vector_pdf(tmp_path / "figures" / "fig1.pdf")

    result = emit_project(docx, "revtex4-2", tmp_path / "out", figure_sources=OverrideSources())

    assert result.figures[0].source.value == "override"
    assert result.figures[1].source.value == "embedded"
