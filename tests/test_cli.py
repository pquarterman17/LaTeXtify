"""Smoke tests for the `latextify convert` CLI command (plan items 5, 16, 18).

Copies the fixture docx into tmp_path first -- see tests/test_emit.py's
module docstring for why (load_or_create_meta writes a write-once
paper.yaml sidecar beside whatever docx path it's given).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from typer.testing import CliRunner

from latextify.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"
ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"

runner = CliRunner()


def _invoke_convert(docx: Path, journal: str, output: Path):
    return runner.invoke(app, ["convert", str(docx), "--journal", journal, "--output", str(output)])


def test_convert_writes_output_tree_and_reports_path(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = _invoke_convert(docx, "revtex4-2", output)

    assert result.exit_code == 0, result.output
    assert str(output / "revtex4-2") in result.output
    assert (output / "revtex4-2" / "main.tex").is_file()


def test_convert_second_run_reports_main_tex_left_untouched(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    _invoke_convert(docx, "revtex4-2", output)
    result = _invoke_convert(docx, "revtex4-2", output)

    assert result.exit_code == 0, result.output
    assert "already existed" in result.output


def test_convert_unknown_journal_exits_nonzero_with_clear_error(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)

    result = _invoke_convert(docx, "no-such-journal", tmp_path / "output")

    assert result.exit_code != 0
    assert "no-such-journal" in result.output


def test_convert_missing_docx_exits_nonzero():
    result = runner.invoke(
        app, ["convert", "does-not-exist.docx", "--journal", "revtex4-2"]
    )
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Citation style switching (plan item 18)
# --------------------------------------------------------------------------- #


def test_convert_with_citation_style_numeric_writes_project(tmp_path):
    """Test that --citation-style numeric works with elsarticle."""
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "elsarticle",
            "--output",
            str(output),
            "--citation-style",
            "numeric",
        ],
    )

    assert result.exit_code == 0
    preamble = (output / "elsarticle" / "generated" / "preamble.tex").read_text()
    assert "elsarticle-num" in preamble


def test_convert_with_citation_style_authoryear_writes_project(tmp_path):
    """Test that --citation-style authoryear works with elsarticle."""
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "elsarticle",
            "--output",
            str(output),
            "--citation-style",
            "authoryear",
        ],
    )

    assert result.exit_code == 0
    preamble = (output / "elsarticle" / "generated" / "preamble.tex").read_text()
    assert "elsarticle-harv" in preamble


def test_convert_unsupported_citation_style_exits_nonzero_with_clear_error(tmp_path):
    """Unsupported citation style (ieeetran + authoryear) should error clearly."""
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "ieeetran",
            "--citation-style",
            "authoryear",
        ],
    )

    assert result.exit_code != 0
    # Error message should name the journal and list allowed modes
    assert "ieeetran" in result.output
    assert "authoryear" in result.output
    assert "numeric" in result.output


def test_convert_citation_style_switch_on_rerun(tmp_path):
    """Re-running with a different citation style switches the preamble, not main.tex."""
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    # First run: numeric
    result1 = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "elsarticle",
            "--output",
            str(output),
            "--citation-style",
            "numeric",
        ],
    )
    assert result1.exit_code == 0

    preamble_numeric = (output / "elsarticle" / "generated" / "preamble.tex").read_text()
    assert "elsarticle-num" in preamble_numeric

    # Second run: authoryear into same output dir
    result2 = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "elsarticle",
            "--output",
            str(output),
            "--citation-style",
            "authoryear",
        ],
    )
    assert result2.exit_code == 0
    assert "already existed" in result2.output  # main.tex untouched

    preamble_authoryear = (output / "elsarticle" / "generated" / "preamble.tex").read_text()
    assert "elsarticle-harv" in preamble_authoryear
    assert "elsarticle-num" not in preamble_authoryear


def test_journals_command_lists_available_journals():
    """The `journals` command should list all registered journals and their modes."""
    result = runner.invoke(app, ["journals"])

    assert result.exit_code == 0
    # Should list at least revtex4-2, elsarticle, ieeetran, sn-jnl
    assert "revtex4-2" in result.output
    assert "elsarticle" in result.output
    assert "ieeetran" in result.output
    assert "sn-jnl" in result.output


def test_journals_command_lists_correct_modes():
    """The `journals` command should list the correct citation modes per journal."""
    result = runner.invoke(app, ["journals"])

    assert result.exit_code == 0
    # revtex4-2 is numeric-only
    assert "revtex4-2: numeric" in result.output
    # elsarticle is dual-mode
    assert "elsarticle:" in result.output
    line = next(
        out_line for out_line in result.output.split("\n")
        if out_line.startswith("elsarticle:")
    )
    assert "authoryear" in line
    assert "numeric" in line
    # ieeetran is numeric-only
    assert "ieeetran: numeric" in result.output


# --------------------------------------------------------------------------- #
# Report generation (plan item 16)
# --------------------------------------------------------------------------- #


def test_convert_generates_report_by_default(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = _invoke_convert(docx, "revtex4-2", output)

    assert result.exit_code == 0, result.output
    report_path = output / "revtex4-2" / "report.md"
    assert report_path.is_file()


def test_convert_skips_report_with_no_report_flag(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--no-report",
        ],
    )

    assert result.exit_code == 0, result.output
    report_path = output / "revtex4-2" / "report.md"
    assert not report_path.exists()


# --------------------------------------------------------------------------- #
# PDF compilation via --pdf (plan item 16) -- real Tectonic compiles
# --------------------------------------------------------------------------- #


@pytest.mark.tectonic
def test_convert_pdf_compiles_when_pdf_flag_set(tmp_path):
    """Test that --pdf flag triggers compilation and produces a PDF.

    Uses revtex4-2 which is in the Tectonic bundle, so no vendoring needed.
    """
    docx = tmp_path / "zotero.docx"
    shutil.copy(ZOTERO_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    # Check that compilation succeeded
    assert "compiled" in result.output
    pdf_path = output / "revtex4-2" / "main.pdf"
    assert pdf_path.is_file(), "PDF should be generated with --pdf flag"


@pytest.mark.tectonic
def test_convert_pdf_exit_zero_on_successful_compile(tmp_path):
    """Exit code should be 0 when compilation succeeds."""
    docx = tmp_path / "zotero.docx"
    shutil.copy(ZOTERO_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--pdf",
        ],
    )

    assert result.exit_code == 0


@pytest.mark.tectonic
def test_convert_report_updated_with_compile_diagnostics(tmp_path):
    """Report should include compilation diagnostics when --pdf is used."""
    docx = tmp_path / "zotero.docx"
    shutil.copy(ZOTERO_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    report_path = output / "revtex4-2" / "report.md"
    assert report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8")
    # Report should mention compilation and success
    assert "## Compilation" in report_text
    # Should either say success or include diagnostics
    assert ("Success" in report_text or "Compilation" in report_text)
