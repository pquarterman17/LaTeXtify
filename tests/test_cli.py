"""Smoke tests for the `latextify convert` CLI command (plan items 5, 16, 18, 21).

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
from latextify.compile.tectonic import find_tectonic

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"
ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"
SUPPLEMENT_DOCX = FIXTURES / "supplement.docx"

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


def test_convert_exclude_figures_emits_text_only_project(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = runner.invoke(
        app,
        ["convert", str(docx), "--journal", "revtex4-2", "--output", str(output),
         "--exclude-figures"],
    )

    assert result.exit_code == 0, result.output
    body = (output / "revtex4-2" / "generated" / "body.tex").read_text(encoding="utf-8")
    assert "\\includegraphics" not in body
    assert "%%FIGURE:" not in body
    # No images were copied into the tree.
    figures_dir = output / "revtex4-2" / "figures"
    assert not any(p.name.startswith("fig") for p in figures_dir.iterdir())


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
# In-table figure resolution: an Image nested inside a table cell (bug found
# by the ingest bug-hunter -- see git history for the original xfail repro
# and its OUT-OF-BOUNDS note). latextify.ingest.filters.plant_anchors walks
# the WHOLE document tree, so a table-nested image was already getting a
# %%FIGURE:<n>%% anchor; latextify.figures.extract now walks the whole tree
# too (via the same doc.walk() mechanism) so it produces a matching Figure
# record -- with Figure.in_table=True, since a float environment
# (\begin{figure}) is not legal LaTeX inside a tabular/longtable cell.
# latextify.emit.project resolves an in_table anchor to a bare, width-limited
# \includegraphics instead of the usual figure environment.
# --------------------------------------------------------------------------- #


def _docx_with_table_image(
    tmp_path: Path, *, image_before: bool = False, image_after: bool = False
):
    """Build a manuscript with one image embedded in a table cell, optionally
    flanked by a top-level image immediately before and/or after the table --
    used to prove anchor<->Figure-record numbering stays aligned regardless
    of which side of the table-nested image a top-level image sits on."""
    docx_module = pytest.importorskip("docx")
    pil_image = pytest.importorskip("PIL.Image")

    def make_png(name: str, color: str) -> Path:
        path = tmp_path / name
        pil_image.new("RGB", (4, 4), color=color).save(path)
        return path

    doc = docx_module.Document()
    doc.add_paragraph(style="Title").add_run("Table With Embedded Image")

    if image_before:
        doc.add_paragraph("Intro text before the table.")
        doc.add_picture(str(make_png("before.png", "red")), width=None)

    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Label"
    run = table.cell(0, 1).paragraphs[0].add_run()
    run.add_picture(str(make_png("table.png", "blue")), width=None)

    if image_after:
        doc.add_paragraph("Outro text after the table.")
        doc.add_picture(str(make_png("after.png", "green")), width=None)

    docx_path = tmp_path / "table_with_image.docx"
    doc.save(docx_path)
    return docx_path


def test_image_in_table_cell_resolves_without_unresolved_placeholder(tmp_path):
    docx_path = _docx_with_table_image(tmp_path)

    result = _invoke_convert(docx_path, "revtex4-2", tmp_path / "output")

    assert result.exit_code == 0, result.output
    assert "unresolved figure" not in result.output.lower()

    output_dir = tmp_path / "output" / "revtex4-2"
    body_tex = (output_dir / "generated" / "body.tex").read_text(encoding="utf-8")

    assert "UNRESOLVED FIGURE" not in body_tex
    assert "%%FIGURE" not in body_tex

    # A float environment is not legal LaTeX inside a table cell -- the
    # in-table image must resolve to a bare \includegraphics, never a
    # \begin{figure} wrapper (this manuscript's only image is the in-table
    # one, so \begin{figure} must not appear anywhere in the body at all).
    assert "\\begin{figure}" not in body_tex
    assert "\\includegraphics[width=3cm]{figures/fig1" in body_tex

    # The image file itself landed in figures/.
    figures_dir = output_dir / "figures"
    assert any(figures_dir.glob("fig1.*")), (
        f"no figures/fig1.* file in {figures_dir}: {list(figures_dir.iterdir())}"
    )


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
def test_image_in_table_cell_compiles_end_to_end(tmp_path):
    docx_path = _docx_with_table_image(tmp_path)

    result = runner.invoke(
        app,
        [
            "convert",
            str(docx_path),
            "--journal",
            "revtex4-2",
            "--output",
            str(tmp_path / "output"),
            "--pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "compiled" in result.output
    pdf_path = tmp_path / "output" / "revtex4-2" / "main.pdf"
    assert pdf_path.is_file()


@pytest.mark.parametrize(
    "image_before, image_after",
    [
        pytest.param(True, False, id="top_level_before_table"),
        pytest.param(False, True, id="top_level_after_table"),
    ],
)
def test_anchor_numbering_stays_aligned_around_a_table_image(
    tmp_path, image_before, image_after
):
    """Regression guard for the anchor<->Figure-record desync bug: whether
    the top-level image sits before or after the table, the table-nested
    image's anchor number must resolve to a real figure (not an unresolved
    placeholder), and every figure number must resolve to exactly one
    \\includegraphics -- no number is skipped or double-claimed."""
    docx_path = _docx_with_table_image(
        tmp_path, image_before=image_before, image_after=image_after
    )

    result = _invoke_convert(docx_path, "revtex4-2", tmp_path / "output")

    assert result.exit_code == 0, result.output
    output_dir = tmp_path / "output" / "revtex4-2"
    body_tex = (output_dir / "generated" / "body.tex").read_text(encoding="utf-8")

    assert "UNRESOLVED FIGURE" not in body_tex
    assert "%%FIGURE" not in body_tex

    # Two images total (one top-level, one in-table) -> figures 1 and 2, each
    # resolved exactly once: one as a normal figure environment (the
    # top-level image, \linewidth-bounded), one as a bare width-limited
    # \includegraphics (the in-table image, an absolute cap) -- regardless of
    # which side of the table it sits on.
    float_includes = body_tex.count("\\includegraphics[width=\\linewidth]{figures/fig")
    bare_includes = body_tex.count("\\includegraphics[width=3cm]{figures/fig")
    assert float_includes == 1
    assert bare_includes == 1
    assert body_tex.count("\\begin{figure}") == 1

    figures_dir = output_dir / "figures"
    for number in (1, 2):
        assert any(figures_dir.glob(f"fig{number}.*")), (
            f"no figures/fig{number}.* file in {figures_dir}: "
            f"{list(figures_dir.iterdir())}"
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


# --------------------------------------------------------------------------- #
# Supplementary material: --supplement (plan item 21)
# --------------------------------------------------------------------------- #


def test_convert_with_supplement_writes_supplement_tex(tmp_path):
    docx = tmp_path / "zotero.docx"
    shutil.copy(ZOTERO_DOCX, docx)
    supplement = tmp_path / "supplement.docx"
    shutil.copy(SUPPLEMENT_DOCX, supplement)
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
            "--supplement",
            str(supplement),
        ],
    )

    assert result.exit_code == 0, result.output
    supplement_tex = output / "revtex4-2" / "supplement.tex"
    assert supplement_tex.is_file()

    bib = (output / "revtex4-2" / "references.bib").read_text(encoding="utf-8")
    # Shared DOI deduped to one entry; new SI-only reference also present.
    assert bib.count("10.1103/PhysRevB.101.045123") == 1
    assert "10.1103/PhysRevApplied.15.054001" in bib

    figures_dir = output / "revtex4-2" / "figures"
    assert sorted(p.name for p in figures_dir.iterdir()) == ["figS1.png", "figS2.png"]


def test_convert_without_supplement_has_no_supplement_tex(tmp_path):
    docx = tmp_path / "figures.docx"
    shutil.copy(FIGURES_DOCX, docx)
    output = tmp_path / "output"

    result = _invoke_convert(docx, "revtex4-2", output)

    assert result.exit_code == 0, result.output
    assert not (output / "revtex4-2" / "supplement.tex").exists()


def test_convert_missing_supplement_docx_exits_nonzero():
    result = runner.invoke(
        app,
        [
            "convert",
            "does-not-exist-main.docx",
            "--journal",
            "revtex4-2",
            "--supplement",
            "does-not-exist-si.docx",
        ],
    )
    assert result.exit_code != 0


@pytest.mark.tectonic
def test_convert_supplement_pdf_compiles_both_documents(tmp_path):
    docx = tmp_path / "zotero.docx"
    shutil.copy(ZOTERO_DOCX, docx)
    supplement = tmp_path / "supplement.docx"
    shutil.copy(SUPPLEMENT_DOCX, supplement)
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
            "--supplement",
            str(supplement),
            "--pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output / "revtex4-2" / "main.pdf").is_file()
    assert (output / "revtex4-2" / "supplement.pdf").is_file()

    report_text = (output / "revtex4-2" / "report.md").read_text(encoding="utf-8")
    assert "## Supplement" in report_text
    assert "S-figures: 2." in report_text


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


# --------------------------------------------------------------------------- #
# Batch conversion (plan item 20)
# --------------------------------------------------------------------------- #


def test_batch_with_three_files_creates_per_file_subdirs(tmp_path):
    """Batch mode should create per-file subdirectories under output/<stem>/<journal>/."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()

    # Copy three fixtures into the batch folder.
    shutil.copy(FIGURES_DOCX, batch_dir / "figures.docx")
    shutil.copy(ZOTERO_DOCX, batch_dir / "zotero_cited.docx")
    shutil.copy(FIGURES_DOCX, batch_dir / "clean.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(batch_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "No .docx files found" not in result.output
    # Each file should have its own per-stem subdirectory.
    assert (output / "figures" / "revtex4-2" / "main.tex").is_file()
    assert (output / "zotero_cited" / "revtex4-2" / "main.tex").is_file()
    assert (output / "clean" / "revtex4-2" / "main.tex").is_file()
    # Each should have a report.md.
    assert (output / "figures" / "revtex4-2" / "report.md").is_file()
    assert (output / "zotero_cited" / "revtex4-2" / "report.md").is_file()
    assert (output / "clean" / "revtex4-2" / "report.md").is_file()


def test_batch_writes_summary_md_to_output_root(tmp_path):
    """Batch mode should write batch_summary.md to the output root."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    shutil.copy(FIGURES_DOCX, batch_dir / "test1.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(batch_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    summary_path = output / "batch_summary.md"
    assert summary_path.is_file()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "Batch Conversion Summary" in summary_text
    assert "journal: revtex4-2" in summary_text.lower()
    assert "test1" in summary_text


def test_batch_with_corrupt_docx_continues_and_marks_error(tmp_path):
    """Batch mode should continue on corrupt .docx, mark it as error, exit 1."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()

    # One good file, one corrupt.
    shutil.copy(FIGURES_DOCX, batch_dir / "good.docx")
    bogus = batch_dir / "corrupt.docx"
    bogus.write_text("Not a real docx file", encoding="utf-8")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(batch_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    # Exit code should be 1 because one file errored.
    assert result.exit_code == 1
    # But the good file should still have been processed.
    assert (output / "good" / "revtex4-2" / "main.tex").is_file()
    # Summary should show error for corrupt file.
    summary_path = output / "batch_summary.md"
    assert summary_path.is_file()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "corrupt" in summary_text.lower() or "error" in summary_text.lower()


def test_batch_skips_temp_files(tmp_path):
    """Batch mode should skip Word temp files like ~$name.docx."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()

    shutil.copy(FIGURES_DOCX, batch_dir / "good.docx")
    # Plant a temp file that should be skipped.
    temp_file = batch_dir / "~$temp.docx"
    temp_file.write_text("temp", encoding="utf-8")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(batch_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    # Only the good file should have been converted.
    assert (output / "good" / "revtex4-2" / "main.tex").is_file()
    # Temp file should NOT have created an output directory.
    assert not (output / "~$temp").exists()
    # Summary should only mention one file.
    summary_path = output / "batch_summary.md"
    summary_text = summary_path.read_text(encoding="utf-8")
    # Count the data rows in the table (not counting header rows).
    assert summary_text.count("| good") == 1
    assert "~$temp" not in summary_text


def test_batch_empty_folder_exits_zero_with_message(tmp_path):
    """Empty batch folder should exit 0 with a clear message."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(empty_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert "No .docx files found" in result.output


def test_batch_recursive_finds_nested_files(tmp_path):
    """--recursive should find .docx files in subdirectories."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    subdir = batch_dir / "subdir"
    subdir.mkdir()

    # One file in root, one in subdir.
    shutil.copy(FIGURES_DOCX, batch_dir / "root.docx")
    shutil.copy(ZOTERO_DOCX, subdir / "nested.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        [
            "batch",
            str(batch_dir),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--recursive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output / "root" / "revtex4-2" / "main.tex").is_file()
    assert (output / "nested" / "revtex4-2" / "main.tex").is_file()


def test_batch_non_recursive_ignores_nested_files(tmp_path):
    """Without --recursive, nested .docx files should be ignored."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    subdir = batch_dir / "subdir"
    subdir.mkdir()

    # One file in root, one in subdir.
    shutil.copy(FIGURES_DOCX, batch_dir / "root.docx")
    shutil.copy(ZOTERO_DOCX, subdir / "nested.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        [
            "batch",
            str(batch_dir),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    # Only root file should be processed.
    assert (output / "root" / "revtex4-2" / "main.tex").is_file()
    # Nested file should NOT have been processed.
    assert not (output / "nested").exists()


@pytest.mark.tectonic
def test_batch_with_pdf_flag_compiles_each_file(tmp_path):
    """Batch mode with --pdf should compile each file to PDF."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    shutil.copy(FIGURES_DOCX, batch_dir / "file1.docx")
    shutil.copy(ZOTERO_DOCX, batch_dir / "file2.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        [
            "batch",
            str(batch_dir),
            "--journal",
            "revtex4-2",
            "--output",
            str(output),
            "--pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    # Each file should have compiled to a PDF.
    assert (output / "file1" / "revtex4-2" / "main.pdf").is_file()
    assert (output / "file2" / "revtex4-2" / "main.pdf").is_file()


def test_batch_summary_table_deterministic_order(tmp_path):
    """Batch summary should list files in deterministic (sorted) order."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()

    # Create files in non-alphabetical order.
    for name in ["zebra.docx", "apple.docx", "middle.docx"]:
        shutil.copy(FIGURES_DOCX, batch_dir / name)

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["batch", str(batch_dir), "--journal", "revtex4-2", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    # Check that summary lists them in sorted order.
    summary_path = output / "batch_summary.md"
    summary_text = summary_path.read_text(encoding="utf-8")
    # Find the positions of each filename in the summary.
    apple_pos = summary_text.find("apple")
    middle_pos = summary_text.find("middle")
    zebra_pos = summary_text.find("zebra")
    # All should be found and in order.
    assert apple_pos < middle_pos < zebra_pos, "Files should be listed in sorted order"


def test_batch_with_unknown_journal_exits_1_with_error(tmp_path):
    """Batch mode with invalid journal should fail all files and exit 1."""
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    shutil.copy(FIGURES_DOCX, batch_dir / "test.docx")

    output = tmp_path / "output"
    result = runner.invoke(
        app,
        [
            "batch",
            str(batch_dir),
            "--journal",
            "no-such-journal",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert "no-such-journal" in result.output or "error" in result.output.lower()



# --------------------------------------------------------------------------- #
# Interactive reference review (--review) glue: _run_interactive_review
# --------------------------------------------------------------------------- #


def _review_fixture(tmp_path, *, flagged=True):
    """An EmitResult + on-disk references.bib for review-glue tests."""
    from latextify.citations.bib import entries_to_bib
    from latextify.model.emit import EmitResult
    from latextify.model.refs import Name, RefEntry
    from latextify.model.validate import (
        FieldCheck,
        ValidationRecord,
        ValidationReport,
    )

    entry = RefEntry(
        key="smith2019", entry_type="article", title="A Study of Widgets",
        authors=(Name(family="Smith", given="Jane"),), year="2019",
        container_title="Journal of Widgets", volume="12", pages="45", doi="10.1/abc",
    )
    canonical = RefEntry(
        key="smith2019", entry_type="article", title="A Study of Widgets",
        authors=(Name(family="Smith", given="Jane"),), year="2020",
        container_title="Journal of Widgets", volume="12", pages="45", doi="10.1/abc",
    )
    records = (
        ValidationRecord(
            key="smith2019", status="mismatch", doi="10.1/abc",
            checks=(FieldCheck(field="year", ours="2019", canonical="2020", ok=False),),
            canonical_entry=canonical,
        ),
    ) if flagged else (
        ValidationRecord(key="smith2019", status="verified", doi="10.1/abc"),
    )
    bib_path = tmp_path / "references.bib"
    bib_path.write_text(entries_to_bib([entry]), encoding="utf-8")

    result = EmitResult(
        output_dir=tmp_path, journal_name="revtex4-2",
        main_tex_path=tmp_path / "main.tex", main_tex_written=True,
        preamble_tex_path=tmp_path / "preamble.tex",
        metadata_tex_path=tmp_path / "metadata.tex",
        body_tex_path=tmp_path / "body.tex", bib_path=bib_path,
        figures_dir=tmp_path / "figures", figure_count=0, citation_count=1,
        validation=ValidationReport(records=records), entries=(entry,),
    )
    return result, bib_path


def test_review_applies_approved_correction_to_bib(tmp_path, monkeypatch):
    import builtins

    from latextify.cli import _run_interactive_review

    result, bib_path = _review_fixture(tmp_path)
    monkeypatch.setattr("latextify.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _msg="": "a")  # approve

    _run_interactive_review(result)

    corrected = bib_path.read_text(encoding="utf-8")
    # The year FIELD is corrected to 2020 (the cite key still contains "2019").
    assert "year = {2020}" in corrected
    assert "year = {2019}" not in corrected


def test_review_deny_leaves_bib_untouched(tmp_path, monkeypatch):
    import builtins

    from latextify.cli import _run_interactive_review

    result, bib_path = _review_fixture(tmp_path)
    before = bib_path.read_text(encoding="utf-8")
    monkeypatch.setattr("latextify.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _msg="": "d")  # deny

    _run_interactive_review(result)

    assert bib_path.read_text(encoding="utf-8") == before


def test_review_non_tty_skips_with_warning(tmp_path, monkeypatch, capsys):
    from latextify.cli import _run_interactive_review

    result, bib_path = _review_fixture(tmp_path)
    before = bib_path.read_text(encoding="utf-8")
    monkeypatch.setattr("latextify.cli.sys.stdin.isatty", lambda: False)

    _run_interactive_review(result)

    assert bib_path.read_text(encoding="utf-8") == before
    assert "needs a terminal" in capsys.readouterr().err


def test_review_nothing_flagged_is_noop(tmp_path, monkeypatch):
    from latextify.cli import _run_interactive_review

    result, bib_path = _review_fixture(tmp_path, flagged=False)
    before = bib_path.read_text(encoding="utf-8")
    # isatty should not even be consulted, but stub it defensively.
    monkeypatch.setattr("latextify.cli.sys.stdin.isatty", lambda: True)

    _run_interactive_review(result)

    assert bib_path.read_text(encoding="utf-8") == before
