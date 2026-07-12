"""Tests for supplementary material handling (plan item 21).

Copies fixtures into tmp_path first -- see tests/test_emit.py's module
docstring for why (load_or_create_meta writes a write-once paper.yaml
sidecar beside whatever docx path it's given).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from latextify.emit.project import emit_project

FIXTURES = Path(__file__).parent / "fixtures"
ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"
FIGURES_DOCX = FIXTURES / "figures.docx"
SUPPLEMENT_DOCX = FIXTURES / "supplement.docx"


def _copy_fixture(tmp_path: Path, src: Path, name: str | None = None) -> Path:
    dest = tmp_path / (name or src.name)
    shutil.copy(src, dest)
    return dest


# --------------------------------------------------------------------------- #
# Without --supplement: byte-identical to today
# --------------------------------------------------------------------------- #


def test_no_supplement_argument_leaves_main_output_unaffected(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)

    with_default = emit_project(docx, "revtex4-2", tmp_path / "output_a")

    docx2 = _copy_fixture(tmp_path, ZOTERO_DOCX, "zotero2.docx")
    explicit_none = emit_project(
        docx2, "revtex4-2", tmp_path / "output_b", supplement_docx_path=None
    )

    assert with_default.supplement is None
    assert explicit_none.supplement is None

    body_a = with_default.body_tex_path.read_text(encoding="utf-8")
    body_b = explicit_none.body_tex_path.read_text(encoding="utf-8")
    assert body_a == body_b

    bib_a = with_default.bib_path.read_text(encoding="utf-8")
    bib_b = explicit_none.bib_path.read_text(encoding="utf-8")
    assert bib_a == bib_b

    assert not (with_default.output_dir / "supplement.tex").exists()


def test_no_supplement_report_shows_none_section(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    report_text = result.report_path.read_text(encoding="utf-8")
    assert "## Supplement\n_None_" in report_text


# --------------------------------------------------------------------------- #
# supplement.tex: write-once, edit-survives-rerun (same contract as main.tex)
# --------------------------------------------------------------------------- #


def test_supplement_tex_written_once(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    assert result.supplement is not None
    assert result.supplement.supplement_tex_written is True
    supplement_tex_path = result.supplement.supplement_tex_path
    assert supplement_tex_path.is_file()
    assert supplement_tex_path.parent == result.output_dir
    assert supplement_tex_path.name == "supplement.tex"


def test_supplement_tex_inputs_generated_files(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    supplement_tex = result.supplement.supplement_tex_path.read_text(encoding="utf-8")
    assert "\\input{generated/supplement_preamble}" in supplement_tex
    assert "\\input{generated/supplement_metadata}" in supplement_tex
    assert "\\input{generated/supplement_body}" in supplement_tex
    assert "\\input{generated/supplement_bibliography}" in supplement_tex
    assert "\\begin{document}" in supplement_tex
    assert "\\end{document}" in supplement_tex


def test_supplement_tex_edit_survives_rerun_and_generated_files_regenerate(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)
    output_root = tmp_path / "output"

    result1 = emit_project(docx, "revtex4-2", output_root, supplement_docx_path=supplement)
    assert result1.supplement.supplement_tex_written is True

    supplement_tex_path = result1.supplement.supplement_tex_path
    edited = "% USER EDIT MARKER -- hand customization\n" + supplement_tex_path.read_text(
        encoding="utf-8"
    )
    supplement_tex_path.write_text(edited, encoding="utf-8")

    # Corrupt a regenerated supplement file to prove it gets rewritten.
    result1.supplement.supplement_body_tex_path.write_text(
        "CORRUPTED PLACEHOLDER", encoding="utf-8"
    )

    result2 = emit_project(docx, "revtex4-2", output_root, supplement_docx_path=supplement)

    assert result2.supplement.supplement_tex_written is False
    assert supplement_tex_path.read_text(encoding="utf-8") == edited

    regenerated_body = result2.supplement.supplement_body_tex_path.read_text(encoding="utf-8")
    assert regenerated_body != "CORRUPTED PLACEHOLDER"
    assert "\\includegraphics{figures/figS1.png}" in regenerated_body

    # main.tex is completely unaffected by the supplement's own rerun.
    assert result2.main_tex_written is False


# --------------------------------------------------------------------------- #
# S-numbering
# --------------------------------------------------------------------------- #


def test_supplement_preamble_carries_s_numbering_commands(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    preamble = result.supplement.supplement_preamble_tex_path.read_text(encoding="utf-8")
    assert "\\renewcommand{\\thefigure}{S\\arabic{figure}}" in preamble
    assert "\\renewcommand{\\thetable}{S\\arabic{table}}" in preamble
    assert "\\renewcommand{\\theequation}{S\\arabic{equation}}" in preamble
    assert "\\renewcommand{\\thesection}{S\\arabic{section}}" in preamble
    # The journal preamble itself (class, packages, bibstyle) is reused verbatim.
    assert "\\documentclass" in preamble
    assert "\\bibliographystyle" in preamble


def test_main_preamble_has_no_s_numbering(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    main_preamble = result.preamble_tex_path.read_text(encoding="utf-8")
    assert "\\thefigure" not in main_preamble


# --------------------------------------------------------------------------- #
# Title block: derived from main Meta, no metadata guessing on the SI docx
# --------------------------------------------------------------------------- #


def test_supplement_title_derived_from_main_title_no_guessing(tmp_path):
    # Main and SI docx live in DIFFERENT directories here (unlike the other
    # tests in this file) specifically so their paper.yaml sidecar paths
    # (docx_path.with_name("paper.yaml")) don't collide -- proving no
    # metadata guessing/sidecar-writing happens for the SI docx at all.
    main_dir = tmp_path / "main"
    si_dir = tmp_path / "si"
    main_dir.mkdir()
    si_dir.mkdir()
    docx = _copy_fixture(main_dir, ZOTERO_DOCX)
    supplement = _copy_fixture(si_dir, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    main_metadata = result.metadata_tex_path.read_text(encoding="utf-8")
    supplement_metadata = result.supplement.supplement_metadata_tex_path.read_text(
        encoding="utf-8"
    )

    # Pull the main \title{...} text out and confirm it reappears prefixed.
    main_title_start = main_metadata.index("\\title{") + len("\\title{")
    main_title_end = main_metadata.index("}\n", main_title_start)
    main_title = main_metadata[main_title_start:main_title_end]

    assert f"\\title{{Supplementary Material: {main_title}}}" in supplement_metadata

    # A sidecar WAS written next to the main docx (metadata guessing ran).
    assert (docx.with_name("paper.yaml")).exists()
    # No sidecar was written next to the SI docx (no metadata guessing on it).
    assert not (supplement.with_name("paper.yaml")).exists()


# --------------------------------------------------------------------------- #
# Figures: S-prefixed file names, main-document figures unaffected
# --------------------------------------------------------------------------- #


def test_supplement_figures_land_as_figs_prefixed_files(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    copied = sorted(p.name for p in result.figures_dir.iterdir())
    assert copied == ["figS1.png", "figS2.png"]
    assert result.supplement.figure_count == 2


def test_main_document_figures_unaffected_by_supplement(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)

    without_supplement = emit_project(docx, "revtex4-2", tmp_path / "output_a")

    docx2 = _copy_fixture(tmp_path, FIGURES_DOCX, "figures2.docx")
    supplement2 = _copy_fixture(tmp_path, SUPPLEMENT_DOCX, "supplement2.docx")
    with_supplement = emit_project(
        docx2, "revtex4-2", tmp_path / "output_b", supplement_docx_path=supplement2
    )

    # Main document's own figures (fig1/fig2/fig3) are identical either way.
    main_figs_a = sorted(p.name for p in without_supplement.figures_dir.iterdir())
    main_figs_b = sorted(
        p.name for p in with_supplement.figures_dir.iterdir() if not p.name.startswith("figS")
    )
    assert main_figs_a == main_figs_b == ["fig1.png", "fig2.png", "fig3.png"]

    body_a = without_supplement.body_tex_path.read_text(encoding="utf-8")
    body_b = with_supplement.body_tex_path.read_text(encoding="utf-8")
    assert body_a == body_b


def test_supplement_figure_anchors_resolve_to_s_numbered_files_not_main(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)  # main doc has its OWN fig1/fig2/fig3
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    supplement_body = result.supplement.supplement_body_tex_path.read_text(encoding="utf-8")
    assert "\\includegraphics{figures/figS1.png}" in supplement_body
    assert "\\includegraphics{figures/figS2.png}" in supplement_body
    # No unresolved/duplicated-number confusion with the main doc's fig1/fig2.
    assert "\\includegraphics{figures/fig1.png}" not in supplement_body
    assert "%%FIGURE" not in supplement_body

    main_body = result.body_tex_path.read_text(encoding="utf-8")
    assert "figS" not in main_body


# --------------------------------------------------------------------------- #
# Citations: cross-document dedup by DOI, new references still added
# --------------------------------------------------------------------------- #


def test_shared_doi_citation_deduplicates_to_one_bib_entry(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    bib = result.bib_path.read_text(encoding="utf-8")
    # The shared DOI (zotero_cited.docx's ARTICLE == supplement.docx's
    # SHARED_ARTICLE) must appear exactly once in the merged bibliography.
    assert bib.count("10.1103/PhysRevB.101.045123") == 1
    assert bib.count("@article{muller2020quantum,") == 1

    # The SI body cites the SAME key as the main document -- no separate
    # "muller...a" duplicate key was minted for it.
    supplement_body = result.supplement.supplement_body_tex_path.read_text(encoding="utf-8")
    assert "\\cite{muller2020quantum}" in supplement_body


def test_new_si_only_reference_is_added_to_bib(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    bib = result.bib_path.read_text(encoding="utf-8")
    assert "10.1103/PhysRevApplied.15.054001" in bib
    assert "@article{okafor2021extended," in bib

    supplement_body = result.supplement.supplement_body_tex_path.read_text(encoding="utf-8")
    assert "\\cite{okafor2021extended}" in supplement_body

    assert result.supplement.new_reference_count == 1
    assert result.supplement.citation_count == 2


def test_main_document_bib_keys_unchanged_by_supplement_merge(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)

    without_supplement = emit_project(docx, "revtex4-2", tmp_path / "output_a")

    docx2 = _copy_fixture(tmp_path, ZOTERO_DOCX, "zotero2.docx")
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)
    with_supplement = emit_project(
        docx2, "revtex4-2", tmp_path / "output_b", supplement_docx_path=supplement
    )

    main_body_a = without_supplement.body_tex_path.read_text(encoding="utf-8")
    main_body_b = with_supplement.body_tex_path.read_text(encoding="utf-8")
    # The main document's own body.tex (its \cite{} keys) is untouched by
    # whatever the supplement's merge did.
    assert main_body_a == main_body_b


def test_supplement_bibliography_include_has_the_line_when_citations_exist(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    bib_include = (
        result.output_dir / "generated" / "supplement_bibliography.tex"
    ).read_text(encoding="utf-8")
    assert bib_include.strip() == "\\bibliography{references}"


# --------------------------------------------------------------------------- #
# Report: Supplement section content
# --------------------------------------------------------------------------- #


def test_report_supplement_section_has_counts_and_no_warnings(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement
    )

    report_text = result.report_path.read_text(encoding="utf-8")
    assert "## Supplement" in report_text
    assert "supplement.tex` written." in report_text
    assert "S-figures: 2." in report_text
    assert "SI citations: 2 " in report_text
    assert "1 new reference" in report_text
    assert "No supplement-specific warnings." in report_text


# --------------------------------------------------------------------------- #
# Compile: tectonic-marked, both PDFs
# --------------------------------------------------------------------------- #


def _tectonic_available() -> bool:
    from latextify.compile.tectonic import TectonicNotAvailableError, ensure_tectonic

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
def test_main_and_supplement_both_compile_under_revtex(tmp_path):
    from latextify.compile.tectonic import compile_document, ensure_tectonic

    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    supplement = _copy_fixture(tmp_path, SUPPLEMENT_DOCX)

    result = emit_project(
        docx, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement, report=False
    )

    main_result = compile_document(result.main_tex_path, tectonic_path=ensure_tectonic())
    assert main_result.success, main_result.raw_log
    assert main_result.pdf_path is not None and main_result.pdf_path.is_file()

    supplement_result = compile_document(
        result.supplement.supplement_tex_path, tectonic_path=ensure_tectonic()
    )
    assert supplement_result.success, supplement_result.raw_log
    assert supplement_result.pdf_path is not None and supplement_result.pdf_path.is_file()
    assert supplement_result.pdf_path.name == "supplement.pdf"
