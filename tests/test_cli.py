"""Smoke tests for the `latextify convert` CLI command (plan item 5).

Copies the fixture docx into tmp_path first -- see tests/test_emit.py's
module docstring for why (load_or_create_meta writes a write-once
paper.yaml sidecar beside whatever docx path it's given).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from latextify.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"

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
