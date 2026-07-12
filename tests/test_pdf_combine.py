"""--combine-supplement: staple main + supplement PDFs into one file."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from typer.testing import CliRunner

from latextify.cli import app
from latextify.compile.pdf import staple_pdfs

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN_DOCX = FIXTURES / "clean.docx"

runner = CliRunner()


def _make_pdf(path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_staple_concatenates_in_order(tmp_path):
    a = _make_pdf(tmp_path / "a.pdf", 3)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    out = staple_pdfs([a, b], tmp_path / "combined.pdf")
    assert out.is_file()
    assert len(PdfReader(str(out)).pages) == 5


def test_staple_missing_input_raises(tmp_path):
    a = _make_pdf(tmp_path / "a.pdf", 1)
    with pytest.raises(FileNotFoundError):
        staple_pdfs([a, tmp_path / "nope.pdf"], tmp_path / "combined.pdf")


def test_combine_flag_requires_supplement():
    result = runner.invoke(
        app, ["convert", str(CLEAN_DOCX), "-j", "revtex4-2", "--combine-supplement"]
    )
    assert result.exit_code == 1
    assert "requires --supplement" in result.output


def test_combine_flag_requires_pdf():
    # Supplement given but no --pdf: nothing to staple, so it must error early.
    result = runner.invoke(
        app,
        ["convert", str(CLEAN_DOCX), "-j", "revtex4-2",
         "--supplement", str(CLEAN_DOCX), "--combine-supplement"],
    )
    assert result.exit_code == 1
    assert "requires --pdf" in result.output


def test_supplement_onecolumn_requires_supplement():
    result = runner.invoke(
        app, ["convert", str(CLEAN_DOCX), "-j", "revtex4-2", "--supplement-onecolumn"]
    )
    assert result.exit_code == 1
    assert "requires --supplement" in result.output
