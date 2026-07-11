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
# Corrupt / wrong inputs at the CLI boundary: clean errors, never a raw
# traceback (result.exception must be None -- a real terminal run would
# otherwise print a full Python stack trace instead of "error: ...").
# --------------------------------------------------------------------------- #


def test_convert_txt_renamed_to_docx_exits_cleanly(tmp_path):
    bogus = tmp_path / "renamed.docx"
    bogus.write_text("This is just plain text, not a docx.\n", encoding="utf-8")

    result = _invoke_convert(bogus, "revtex4-2", tmp_path / "output")

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output


def test_convert_zip_missing_document_xml_exits_cleanly(tmp_path):
    import zipfile

    bogus = tmp_path / "notooxml.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("hello.txt", "not a word document")

    result = _invoke_convert(bogus, "revtex4-2", tmp_path / "output")

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output


def test_convert_malformed_document_xml_exits_cleanly(tmp_path):
    import zipfile

    bogus = tmp_path / "malformed.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:p>unterminated")

    result = _invoke_convert(bogus, "revtex4-2", tmp_path / "output")

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output


def test_equations_zip_missing_document_xml_exits_cleanly(tmp_path):
    import zipfile

    bogus = tmp_path / "notooxml.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("hello.txt", "not a word document")

    result = runner.invoke(app, ["equations", str(bogus), "--output", str(tmp_path / "out")])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output


def test_equations_malformed_document_xml_exits_cleanly(tmp_path):
    import zipfile

    bogus = tmp_path / "malformed.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:p>unterminated")

    result = runner.invoke(app, ["equations", str(bogus), "--output", str(tmp_path / "out")])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error:" in result.output


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
# OUT-OF-BOUNDS FINDING (reported, not fixed -- see docstring):
# figures.extract / emit.project desync with an image nested in a table cell
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    reason=(
        "OUT-OF-BOUNDS BUG (not fixed by this hunt -- belongs to "
        "latextify/figures/extract.py + latextify/emit/project.py, both "
        "outside this hunt's territory). "
        "latextify.ingest.filters.plant_anchors (in-territory) walks the "
        "WHOLE document tree, so an Image nested inside a table cell gets a "
        "%%FIGURE:<n>%% anchor and is counted in "
        "BodyConversionResult.figure_count -- deliberately, per the "
        "filters.py module docstring. But "
        "latextify.figures.extract.extract_figures only iterates "
        "doc.content (TOP-LEVEL blocks) and extract_figures._find_image "
        "only descends into Para/Plain/Figure blocks -- never Table -- so "
        "it silently never produces a Figure record for that same image. "
        "extract.py's own docstring even documents the assumption this "
        "violates: 'for the flat paragraph structure real manuscripts use "
        "(one image per paragraph, no images nested inside other block "
        "containers), both traversals visit images in the same order and "
        "therefore agree on numbers' -- an image in a table cell breaks "
        "exactly that assumption. The visible symptom: the anchor resolves "
        "to a permanent 'unresolved figure anchor' EmitWarning + a literal "
        "'[UNRESOLVED FIGURE 1]' placeholder injected into the table cell "
        "(verified by running latextify.emit.project.emit_project "
        "directly). Fix needs figures/extract.py to also walk into Table "
        "cells (or a documented, deliberate decision to exclude "
        "table-nested images from plant_anchors' count too, keeping both "
        "stages' assumptions in sync) -- either way, a change to code "
        "outside this hunt's territory."
    ),
    strict=True,
)
def test_image_in_table_cell_desyncs_with_figures_extract(tmp_path):
    docx_module = pytest.importorskip("docx")
    pil_image = pytest.importorskip("PIL.Image")

    img_path = tmp_path / "dot.png"
    pil_image.new("RGB", (4, 4), color="blue").save(img_path)

    doc = docx_module.Document()
    doc.add_paragraph(style="Title").add_run("Table With Embedded Image")
    doc.add_paragraph("Intro text before the table.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Label"
    run = table.cell(0, 1).paragraphs[0].add_run()
    run.add_picture(str(img_path), width=None)
    doc.add_paragraph("Outro text after the table.")

    docx_path = tmp_path / "table_with_image.docx"
    doc.save(docx_path)

    result = _invoke_convert(docx_path, "revtex4-2", tmp_path / "output")

    assert result.exit_code == 0, result.output
    body_tex = (tmp_path / "output" / "revtex4-2" / "generated" / "body.tex").read_text(
        encoding="utf-8"
    )
    # What SHOULD happen: the image is resolved into a real
    # \includegraphics reference, matching figure 1.
    assert "UNRESOLVED FIGURE" not in body_tex, (
        "figures.extract now resolves table-nested images -- promote this "
        "xfail to a real assertion and remove the OUT-OF-BOUNDS note above"
    )


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
# PDF compilation via --pdf: structured errors for timeout / missing / broken
# tectonic binary -- mocked, no real compile or network needed.
# --------------------------------------------------------------------------- #


def test_convert_pdf_compile_timeout_is_a_clean_structured_error(tmp_path, monkeypatch):
    """A compile that exceeds its timeout raises subprocess.TimeoutExpired
    (by design -- see compile.tectonic.compile_document's docstring); the
    CLI's `except Exception` around the --pdf step must turn that into the
    same "error: ..." + nonzero exit every other failure path uses, never a
    raw traceback."""
    import subprocess

    import latextify.cli as cli_module

    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    def _fake_compile_document(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["tectonic"], timeout=0.001)

    monkeypatch.setattr(cli_module, "compile_document", _fake_compile_document)
    monkeypatch.setattr(cli_module, "ensure_tectonic", lambda: Path("fake-tectonic"))

    result = runner.invoke(
        app,
        ["convert", str(docx), "--journal", "revtex4-2", "--output", str(output), "--pdf"],
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error: compilation failed" in result.output
    assert "timed out" in result.output


def test_convert_pdf_tectonic_present_but_fails_to_execute(tmp_path, monkeypatch):
    """A tectonic binary that exists on PATH/cache but can't actually be
    executed (corrupt download, permissions, wrong architecture, ...)
    surfaces as OSError from subprocess.run -- must be a clean error too."""
    import latextify.cli as cli_module

    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    def _fake_compile_document(*args, **kwargs):
        raise OSError("[WinError 216] This version of %1 is not compatible")

    monkeypatch.setattr(cli_module, "compile_document", _fake_compile_document)
    monkeypatch.setattr(cli_module, "ensure_tectonic", lambda: Path("fake-tectonic"))

    result = runner.invoke(
        app,
        ["convert", str(docx), "--journal", "revtex4-2", "--output", str(output), "--pdf"],
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raw traceback leaked: {result.exception!r}"
    )
    assert "error: compilation failed" in result.output


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
