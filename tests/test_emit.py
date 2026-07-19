"""Tests for latextify.emit.project -- the project emitter (plan item 5).

Every test that touches metadata copies its source fixture into ``tmp_path``
first: ``load_or_create_meta`` writes a write-once ``paper.yaml`` sidecar
*beside the docx path it's given*, and the committed fixtures under
tests/fixtures/ must never be mutated by a test run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from latextify.compile.tectonic import find_tectonic
from latextify.emit.project import _copy_figures, _prune_stale_figures, emit_project
from latextify.model import BodyConversionResult
from latextify.model.figure import Figure

FIXTURES = Path(__file__).parent / "fixtures"
FIGURES_DOCX = FIXTURES / "figures.docx"
ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"
METADATA_DOCX = FIXTURES / "metadata_titlepage.docx"
CLEAN_DOCX = FIXTURES / "clean.docx"  # citation-free, figure-free manuscript


def _copy_fixture(tmp_path: Path, src: Path) -> Path:
    dest = tmp_path / src.name
    shutil.copy(src, dest)
    return dest


# --------------------------------------------------------------------------- #
# Tree writer + write-once main.tex + two-run edit survival
# --------------------------------------------------------------------------- #


def test_first_run_writes_full_tree(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    assert result.main_tex_written is True
    assert result.main_tex_path.is_file()
    assert result.preamble_tex_path.is_file()
    assert result.metadata_tex_path.is_file()
    assert result.body_tex_path.is_file()
    assert result.bib_path.is_file()
    assert result.figures_dir.is_dir()
    assert result.output_dir == tmp_path / "output" / "revtex4-2"


def test_main_tex_inputs_generated_files_and_bibliography(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    main_tex = result.main_tex_path.read_text(encoding="utf-8")
    assert "\\input{generated/preamble}" in main_tex
    assert "\\input{generated/metadata}" in main_tex
    assert "\\input{generated/body}" in main_tex
    # Plan item 26: the bibliography is included via a regenerated file, NOT a
    # direct \bibliography call in write-once main.tex, so citation-free
    # manuscripts compile under IEEEtran.
    assert "\\input{generated/bibliography}" in main_tex
    assert "\\bibliography{references}" not in main_tex
    assert "\\begin{document}" in main_tex
    assert "\\end{document}" in main_tex


def test_second_run_preserves_manual_main_tex_edit_and_regenerates_body(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    output_root = tmp_path / "output"

    result1 = emit_project(docx, "revtex4-2", output_root)
    assert result1.main_tex_written is True

    edited = "% USER EDIT MARKER -- hand customization\n" + result1.main_tex_path.read_text(
        encoding="utf-8"
    )
    result1.main_tex_path.write_text(edited, encoding="utf-8")

    # Corrupt a generated file to prove it gets unconditionally rewritten.
    result1.body_tex_path.write_text("CORRUPTED PLACEHOLDER", encoding="utf-8")

    result2 = emit_project(docx, "revtex4-2", output_root)

    assert result2.main_tex_written is False
    assert result2.main_tex_path.read_text(encoding="utf-8") == edited

    regenerated_body = result2.body_tex_path.read_text(encoding="utf-8")
    assert regenerated_body != "CORRUPTED PLACEHOLDER"
    assert "\\includegraphics[width=\\linewidth]{figures/fig1.png}" in regenerated_body


def test_generated_files_are_always_overwritten_even_if_hand_edited(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    output_root = tmp_path / "output"

    result1 = emit_project(docx, "revtex4-2", output_root)
    result1.preamble_tex_path.write_text("HAND EDITED PREAMBLE", encoding="utf-8")
    result1.metadata_tex_path.write_text("HAND EDITED METADATA", encoding="utf-8")

    result2 = emit_project(docx, "revtex4-2", output_root)

    assert result2.preamble_tex_path.read_text(encoding="utf-8") != "HAND EDITED PREAMBLE"
    assert result2.metadata_tex_path.read_text(encoding="utf-8") != "HAND EDITED METADATA"
    assert "\\documentclass" in result2.preamble_tex_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Anchor resolution: figures
# --------------------------------------------------------------------------- #


def test_no_unresolved_anchors_in_any_generated_file(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    for path in (
        result.main_tex_path,
        result.preamble_tex_path,
        result.metadata_tex_path,
        result.body_tex_path,
    ):
        assert "%%" not in path.read_text(encoding="utf-8"), path
    assert result.warnings == ()


def test_figures_copied_and_renamed_by_number(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    copied = sorted(p.name for p in result.figures_dir.iterdir())
    assert copied == ["fig1.png", "fig2.png", "fig3.png"]


def test_svg_override_lands_as_pdf_in_output_tree(tmp_path):
    # plan item 15 done-when: "an SVG override lands as PDF in the output tree".
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    (figures_dir / "fig1.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10" fill="blue"/></svg>',
        encoding="utf-8",
    )

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    pdf_path = result.figures_dir / "fig1.pdf"
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert not (result.figures_dir / "fig1.svg").exists()

    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{figures/fig1.pdf}" in body

    # The Figure IR exposes what conversion occurred (plan item 15).
    fig1 = next(f for f in result.figures if f.number == 1)
    assert fig1.source.value == "override"
    assert fig1.conversion_note is not None


def test_figures_yaml_manifest_beats_folder_override_in_full_emit(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)

    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    (figures_dir / "fig2.pdf").write_bytes(b"%PDF-1.4 folder override\n")

    manifest_target = tmp_path / "manifest_fig2.png"
    manifest_target.write_bytes(b"fake-png-manifest-override")
    (tmp_path / "figures.yaml").write_text("2: manifest_fig2.png\n", encoding="utf-8")

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    fig2 = next(f for f in result.figures if f.number == 2)
    assert fig2.source.value == "manifest"
    assert fig2.resolved_path == manifest_target

    copied_path = result.figures_dir / "fig2.png"
    assert copied_path.is_file()
    assert copied_path.read_bytes() == b"fake-png-manifest-override"
    assert not (result.figures_dir / "fig2.pdf").exists()

    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{figures/fig2.png}" in body


def test_eps_override_without_ghostscript_warns_and_passes_through(tmp_path, monkeypatch):
    from latextify.figures import convert as convert_mod

    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)

    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    eps_source = figures_dir / "fig3.eps"
    eps_source.write_text("%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 1 1\n", encoding="utf-8")

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    assert (result.figures_dir / "fig3.eps").is_file()
    assert any("Ghostscript" in w.message and "figure 3" in w.message for w in result.warnings)


def test_wrapped_figure_anchor_resolves_without_duplicate_caption(tmp_path):
    # Figure 1 in figures.docx is promoted by pandoc into its own
    # \begin{figure}...\caption{...}...\end{figure} wrapper carrying a
    # duplicate, label-prefixed caption -- the whole wrapper must be
    # replaced, not just the anchor token.
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert body.count("\\begin{figure}") == 3
    assert body.count("\\end{figure}") == 3
    assert "\\includegraphics[width=\\linewidth]{figures/fig1.png}" in body
    assert "\\caption{A red placeholder figure, captioned via Word's Caption style.}" in body
    # The duplicate, label-prefixed pandoc caption must not survive.
    assert "Figure 1:" not in body


def test_bare_figure_anchor_swallows_adjacent_duplicate_caption_paragraph(tmp_path):
    # Figures 2 and 3 in figures.docx are bare anchors (no pandoc Figure
    # promotion); the raw "Figure N:"/"Fig. N:" caption paragraph pandoc
    # left behind as a sibling block must be removed, not just the anchor.
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert "\\includegraphics[width=\\linewidth]{figures/fig2.png}" in body
    assert "\\caption{A green placeholder figure, captioned via a plain paragraph.}" in body
    assert "\\includegraphics[width=\\linewidth]{figures/fig3.png}" in body
    assert "\\caption{A blue placeholder figure, captioned with the abbreviated label.}" in body
    # The leftover raw caption paragraphs must not survive as separate text.
    assert "Figure 2:" not in body
    assert "Fig. 3:" not in body
    # Each caption's text appears exactly once (inside \caption{}, not duplicated).
    assert body.count("A green placeholder figure, captioned via a plain paragraph.") == 1
    assert body.count("A blue placeholder figure, captioned with the abbreviated label.") == 1


def test_unresolved_figure_anchor_degrades_to_comment_and_warning(tmp_path, monkeypatch):
    import latextify.emit.project as project_mod
    from latextify.ingest.pandoc import convert_docx_to_body as real_convert

    docx = _copy_fixture(tmp_path, FIGURES_DOCX)

    def fake_convert(docx_path, media_dir, **kwargs):
        real = real_convert(docx_path, media_dir, **kwargs)
        return BodyConversionResult(
            tex=real.tex + "\n\nSee also %%FIGURE:99%% for a figure that doesn't exist.\n",
            media_dir=real.media_dir,
            figure_count=real.figure_count,
            citation_count=real.citation_count,
            findings=real.findings,
        )

    monkeypatch.setattr(project_mod, "convert_docx_to_body", fake_convert)

    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert "%%FIGURE:99%%" not in body
    assert "\\textbf{[UNRESOLVED FIGURE 99]}" in body
    assert any("figure 99" in w.message for w in result.warnings)


# --------------------------------------------------------------------------- #
# Text-only emit (exclude_figures / --exclude-figures)
# --------------------------------------------------------------------------- #


def test_exclude_figures_drops_all_figures(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)

    # Sanity: the default run DOES embed figures, so the exclusion assertions
    # below are meaningful (figures.docx carries fig1/fig2/fig3).
    included = emit_project(docx, "revtex4-2", tmp_path / "with-figs")
    assert "\\includegraphics" in included.body_tex_path.read_text(encoding="utf-8")

    result = emit_project(docx, "revtex4-2", tmp_path / "no-figs", exclude_figures=True)
    body = result.body_tex_path.read_text(encoding="utf-8")

    # No image, no leftover anchor, no unresolved placeholder, no float.
    assert "\\includegraphics" not in body
    assert "%%FIGURE:" not in body
    assert "UNRESOLVED FIGURE" not in body
    assert "\\begin{figure}" not in body
    # Nothing copied into figures/, and the result reports zero figures.
    assert not any(p.name.startswith("fig") for p in result.figures_dir.iterdir())
    assert result.figure_count == 0
    assert result.figures == ()
    # Exclusion is a requested mode, not a degradation -- it emits no figure warning.
    assert not any("figure" in w.message.lower() for w in result.warnings)


def test_exclude_figures_clears_images_from_a_prior_included_run(tmp_path):
    # Privacy contract: toggling exclude ON for an existing tree must not leave
    # the previous run's images behind (they would also ride into a .zip export).
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    output = tmp_path / "output"

    first = emit_project(docx, "revtex4-2", output)
    assert any(p.name.startswith("fig") for p in first.figures_dir.iterdir())

    second = emit_project(docx, "revtex4-2", output, exclude_figures=True)
    assert not any(p.name.startswith("fig") for p in second.figures_dir.iterdir())


def test_exclude_figures_strips_even_an_unmatched_anchor(tmp_path, monkeypatch):
    # The included-mode sibling above turns a stray %%FIGURE:99%% into a loud
    # UNRESOLVED placeholder + warning; under exclude it must vanish silently.
    import latextify.emit.project as project_mod
    from latextify.ingest.pandoc import convert_docx_to_body as real_convert

    docx = _copy_fixture(tmp_path, FIGURES_DOCX)

    def fake_convert(docx_path, media_dir, **kwargs):
        real = real_convert(docx_path, media_dir, **kwargs)
        return BodyConversionResult(
            tex=real.tex + "\n\nSee also %%FIGURE:99%% here.\n",
            media_dir=real.media_dir,
            figure_count=real.figure_count,
            citation_count=real.citation_count,
            findings=real.findings,
        )

    monkeypatch.setattr(project_mod, "convert_docx_to_body", fake_convert)

    result = emit_project(docx, "revtex4-2", tmp_path / "output", exclude_figures=True)
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert "%%FIGURE:99%%" not in body
    assert "UNRESOLVED FIGURE" not in body
    assert not any("figure 99" in w.message for w in result.warnings)


# --------------------------------------------------------------------------- #
# Anchor resolution: citations
# --------------------------------------------------------------------------- #
#
# pandoc's docx reader does not recognize the Zotero/Mendeley
# "ADDIN ZOTERO_ITEM CSL_CITATION {json}" / "ADDIN CSL_CITATION {json}"
# field codes as native Cite AST elements (verified empirically against
# zotero_cited.docx on pandoc 3.9 -- see the item 5 executor report), so no
# real fixture in this repo drives a %%CITE:<idx>%% anchor through the real
# pandoc pipeline. These tests prove the emitter's own anchor <-> Citation
# pairing logic is correct by monkeypatching only the pandoc body-conversion
# call and using citations.fields.extract_field_citations's real output
# against zotero_cited.docx for the Citation list.


def _inject_cite_anchors(monkeypatch, tex: str) -> None:
    import latextify.emit.project as project_mod
    from latextify.ingest.pandoc import convert_docx_to_body as real_convert

    def fake_convert(docx_path, media_dir, **kwargs):
        real = real_convert(docx_path, media_dir, **kwargs)
        return BodyConversionResult(
            tex=tex,
            media_dir=real.media_dir,
            figure_count=0,
            citation_count=real.citation_count,
            findings=(),
        )

    monkeypatch.setattr(project_mod, "convert_docx_to_body", fake_convert)


def test_citation_anchors_resolve_to_cite_in_document_order(tmp_path, monkeypatch):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    _inject_cite_anchors(
        monkeypatch,
        "Intro %%CITE:1%%. Foundations %%CITE:2%%. Aside %%CITE:3%%. Recent %%CITE:4%%.\n",
    )

    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert "\\cite{muller2020quantum}" in body
    assert "\\cite{kittel2005introduction,smith2019scalable}" in body
    assert "\\cite{garcia2018topological}" in body
    assert "\\cite{smith2021superconductivity}" in body
    assert "%%CITE" not in body
    assert result.warnings == ()

    # Document order: cite commands appear in the same order as the anchors.
    positions = [body.index(needle) for needle in ["muller2020quantum", "kittel2005introduction",
                                                     "garcia2018topological",
                                                     "smith2021superconductivity"]]
    assert positions == sorted(positions)


def test_unresolved_citation_anchor_degrades_to_comment_and_warning(tmp_path, monkeypatch):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    _inject_cite_anchors(
        monkeypatch, "Only one citation here: %%CITE:1%%. Then a bad one %%CITE:7%%.\n"
    )

    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert "\\cite{muller2020quantum}" in body
    assert "%%CITE:7%%" not in body
    assert "\\textbf{[UNRESOLVED CITATION]}" in body
    assert any("citation anchor 7" in w.message for w in result.warnings)


def test_field_coded_citations_link_via_sentinels_through_real_pipeline(tmp_path):
    # Item 24: the real pipeline plants ZZLTXCITE sentinels pre-pandoc and the
    # emitter resolves them, so field-coded citations pandoc never turns into
    # Cite nodes still reach the body as \cite{...}. bib stays complete and the
    # linkage-gap warning must NOT fire. The nested (index 2) citation links too.
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    bib = result.bib_path.read_text(encoding="utf-8")
    for key in (
        "muller2020quantum",
        "kittel2005introduction",
        "smith2019scalable",
        "garcia2018topological",
        "smith2021superconductivity",
    ):
        assert f"{{{key}," in bib

    assert result.citation_count == 4

    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\cite{muller2020quantum}" in body
    assert "\\cite{kittel2005introduction,smith2019scalable}" in body
    assert "\\cite{garcia2018topological}" in body  # nested inside a PAGEREF field
    assert "\\cite{smith2021superconductivity}" in body
    assert "ZZLTXCITE" not in body  # every sentinel resolved

    assert not any("linked into the body" in w.message for w in result.warnings)


# --------------------------------------------------------------------------- #
# Preamble: hyperref wiring
# --------------------------------------------------------------------------- #


def test_revtex_preamble_already_has_hyperref_and_is_left_alone(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    preamble = result.preamble_tex_path.read_text(encoding="utf-8")
    assert preamble.count("hyperref") == 1  # not duplicated


def _write_bare_journal(tmp_path: Path) -> Path:
    """A minimal journal manifest with no hyperref package, to exercise the append path."""
    journals_dir = tmp_path / "journals"
    jdir = journals_dir / "bare"
    jdir.mkdir(parents=True)
    (jdir / "manifest.yaml").write_text(
        "class: article\n"
        "metadata_scheme: bare\n"
        "bib:\n"
        "  default_mode: numeric\n"
        "  modes:\n"
        "    numeric:\n"
        "      bibstyle: plain\n",
        encoding="utf-8",
    )
    (jdir / "preamble.tex.j2").write_text(
        "\\documentclass{\\VAR{document_class}}\n\\bibliographystyle{\\VAR{bibstyle}}\n",
        encoding="utf-8",
    )
    (jdir / "metadata.tex.j2").write_text(
        "\\title{\\VAR{meta.title}}\n\\maketitle\n",
        encoding="utf-8",
    )
    return journals_dir


def test_hyperref_is_appended_when_journal_preamble_lacks_it(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    journals_dir = _write_bare_journal(tmp_path)

    result = emit_project(
        docx, "bare", tmp_path / "output", journals_dir=journals_dir
    )

    preamble = result.preamble_tex_path.read_text(encoding="utf-8")
    assert "\\usepackage[colorlinks=true" in preamble
    assert "{hyperref}" in preamble


# --------------------------------------------------------------------------- #
# Preamble: \raggedbottom (avoid REVTeX reprint flush-bottom column gaps)
# --------------------------------------------------------------------------- #


def _active_lines(preamble: str) -> list[str]:
    """Non-comment, non-blank preamble lines (a ``\\flushbottom`` in a comment
    is documentation, not a directive)."""
    return [
        ln.strip()
        for ln in preamble.splitlines()
        if ln.strip() and not ln.lstrip().startswith("%")
    ]


def test_raggedbottom_appended_to_revtex_preamble(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    active = _active_lines(result.preamble_tex_path.read_text(encoding="utf-8"))
    assert "\\raggedbottom" in active
    assert "\\flushbottom" not in active


def _write_flushbottom_journal(tmp_path: Path) -> Path:
    """A journal whose template already commits to \\flushbottom (opt-out path)."""
    journals_dir = tmp_path / "journals"
    jdir = journals_dir / "flushed"
    jdir.mkdir(parents=True)
    (jdir / "manifest.yaml").write_text(
        "class: article\n"
        "metadata_scheme: bare\n"
        "bib:\n"
        "  default_mode: numeric\n"
        "  modes:\n"
        "    numeric:\n"
        "      bibstyle: plain\n",
        encoding="utf-8",
    )
    (jdir / "preamble.tex.j2").write_text(
        "\\documentclass{\\VAR{document_class}}\n"
        "\\flushbottom\n"
        "\\bibliographystyle{\\VAR{bibstyle}}\n",
        encoding="utf-8",
    )
    (jdir / "metadata.tex.j2").write_text(
        "\\title{\\VAR{meta.title}}\n\\maketitle\n", encoding="utf-8"
    )
    return journals_dir


def test_raggedbottom_not_added_when_preamble_sets_a_bottom_mode(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    journals_dir = _write_flushbottom_journal(tmp_path)

    result = emit_project(docx, "flushed", tmp_path / "output", journals_dir=journals_dir)

    active = _active_lines(result.preamble_tex_path.read_text(encoding="utf-8"))
    # The template's explicit \flushbottom is respected, not overridden.
    assert "\\flushbottom" in active
    assert "\\raggedbottom" not in active


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


def test_metadata_tex_renders_title_and_authors(tmp_path):
    docx = _copy_fixture(tmp_path, METADATA_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    metadata = result.metadata_tex_path.read_text(encoding="utf-8")
    assert "\\title{Superconducting Gap Anisotropy in Doped Compound X2Y}" in metadata
    assert "\\author{Jane A. Doe}" in metadata
    assert "\\author{John B. Smith}" in metadata
    assert "\\email{jane.doe@example.edu}" in metadata


def test_metadata_reused_from_existing_paper_yaml_on_second_run(tmp_path):
    docx = _copy_fixture(tmp_path, METADATA_DOCX)
    output_root = tmp_path / "output"

    emit_project(docx, "revtex4-2", output_root)
    sidecar = docx.with_name("paper.yaml")
    assert sidecar.is_file()

    # Hand-edit the sidecar; the second run must render from the edited
    # version, not re-guess from the docx (write-once source of truth).
    edited = sidecar.read_text(encoding="utf-8").replace(
        "Superconducting Gap Anisotropy in Doped Compound X2Y", "A Hand-Edited Title"
    )
    sidecar.write_text(edited, encoding="utf-8")

    result = emit_project(docx, "revtex4-2", output_root)
    metadata = result.metadata_tex_path.read_text(encoding="utf-8")
    assert "\\title{A Hand-Edited Title}" in metadata


# --------------------------------------------------------------------------- #
# Journal validation passthrough
# --------------------------------------------------------------------------- #


def test_unknown_journal_raises_manifest_error(tmp_path):
    from latextify.templates.loader import ManifestError

    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    with pytest.raises(ManifestError):
        emit_project(docx, "no-such-journal", tmp_path / "output")


# --------------------------------------------------------------------------- #
# Consolidated report generation (plan item 16)
# --------------------------------------------------------------------------- #


def test_report_md_written_by_default(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    assert result.report_path is not None
    assert result.report_path.is_file()
    assert result.report_path.name == "report.md"


def test_report_skipped_when_report_false(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output", report=False)

    assert result.report_path is None
    assert not (result.output_dir / "report.md").exists()


def test_report_contains_all_sections(tmp_path):
    # Use a fixture that exercises multiple stages: preflight, figures, citations.
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    report_text = result.report_path.read_text(encoding="utf-8")

    # Report should have all four major sections
    assert "## Preflight Findings" in report_text
    assert "## Citation Extraction" in report_text
    assert "## Figures" in report_text
    assert "## Compilation" in report_text

    # Figures section should mention the embedded figures
    assert "Fig 1" in report_text
    assert "Fig 2" in report_text
    assert "Fig 3" in report_text

    # Compilation should say "not compiled"
    assert "Not compiled" in report_text


def test_report_stable_across_runs(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result1 = emit_project(docx, "revtex4-2", tmp_path / "output1")
    report1 = result1.report_path.read_text(encoding="utf-8")

    # Manually copy the docx fixture again for a second run
    docx2 = _copy_fixture(tmp_path, FIGURES_DOCX)
    docx2.rename(tmp_path / "figures2.docx")
    result2 = emit_project(tmp_path / "figures2.docx", "revtex4-2", tmp_path / "output2")
    report2 = result2.report_path.read_text(encoding="utf-8")

    # The reports should be identical except for timestamps (which we ignore for this check).
    # Extract non-timestamp parts for comparison.
    def normalize_report(text):
        lines = text.split("\n")
        return "\n".join(line for line in lines if "Generated:" not in line)

    assert normalize_report(report1) == normalize_report(report2)


# --------------------------------------------------------------------------- #
# Bibliography include -- citation-free manuscripts must compile (plan item 26)
# --------------------------------------------------------------------------- #


def _tectonic_available() -> bool:
    # Detection only -- must NOT download at collection time: anonymous
    # GitHub API calls from CI runners hit rate limits, and unit jobs
    # deselect tectonic tests anyway. ensure_tectonic() still runs (and
    # downloads if needed) inside the marked tests themselves; CI's
    # integration job pre-fetches the binary before pytest.
    return find_tectonic() is not None


def test_bibliography_include_is_a_generated_file_not_in_main_tex(tmp_path):
    # main.tex is write-once; the \bibliography inclusion must live in the
    # regenerated generated/bibliography.tex so it can be omitted for a
    # citation-free document (plan item 26).
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = emit_project(docx, "ieeetran", tmp_path / "output")

    main_tex = result.main_tex_path.read_text(encoding="utf-8")
    assert "\\input{generated/bibliography}" in main_tex
    assert "\\bibliography{references}" not in main_tex

    bib_include = result.output_dir / "generated" / "bibliography.tex"
    assert bib_include.is_file()


def test_generated_bibliography_omits_the_line_when_no_citations(tmp_path):
    # clean.docx has no citations -> references.bib is empty -> the generated
    # bibliography include must NOT contain a \bibliography command (an empty
    # \bibliography breaks IEEEtran's \thebibliography).
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = emit_project(docx, "ieeetran", tmp_path / "output")

    assert result.bib_path.read_text(encoding="utf-8").strip() == ""
    bib_include = (result.output_dir / "generated" / "bibliography.tex").read_text(
        encoding="utf-8"
    )
    # No *active* (uncommented) \bibliography command -- every line is a comment.
    assert not any(
        line.lstrip().startswith("\\bibliography") for line in bib_include.splitlines()
    )
    assert all(
        line.lstrip().startswith("%") or not line.strip()
        for line in bib_include.splitlines()
    )


def test_generated_bibliography_has_the_line_when_citations_exist(tmp_path):
    # A field-coded document DOES have references -> the include carries the
    # real \bibliography{references} line so BibTeX runs as before.
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    assert result.bib_path.read_text(encoding="utf-8").strip() != ""
    bib_include = (result.output_dir / "generated" / "bibliography.tex").read_text(
        encoding="utf-8"
    )
    assert bib_include.strip() == "\\bibliography{references}"


def test_bibliography_include_regenerates_when_citations_appear_on_rerun(tmp_path):
    # Deleting/regenerating generated/ must flip the include appropriately; the
    # include is regenerated content, so a re-run always reflects current refs.
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    output_root = tmp_path / "output"
    result = emit_project(docx, "revtex4-2", output_root)
    bib_include_path = result.output_dir / "generated" / "bibliography.tex"

    # Corrupt the regenerated include; a second run must overwrite it.
    bib_include_path.write_text("CORRUPT", encoding="utf-8")
    result2 = emit_project(docx, "revtex4-2", output_root)
    assert result2.main_tex_written is False
    assert bib_include_path.read_text(encoding="utf-8").strip() == "\\bibliography{references}"


def test_legacy_main_tex_with_direct_bibliography_warns(tmp_path):
    # Backward compat: a pre-item-26 main.tex still carries a direct
    # \bibliography{references} line. main.tex is write-once so we cannot
    # rewrite it -- emit_project must surface a one-line-edit warning.
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    output_root = tmp_path / "output"
    output_dir = output_root / "ieeetran"
    output_dir.mkdir(parents=True)
    legacy_main = (
        "\\input{generated/preamble}\n"
        "\\begin{document}\n"
        "\\input{generated/metadata}\n"
        "\\input{generated/body}\n"
        "\\bibliography{references}\n"
        "\\end{document}\n"
    )
    (output_dir / "main.tex").write_text(legacy_main, encoding="utf-8")

    result = emit_project(docx, "ieeetran", output_root)

    assert result.main_tex_written is False
    # The legacy main.tex is preserved untouched (write-once contract).
    assert (output_dir / "main.tex").read_text(encoding="utf-8") == legacy_main
    assert any(
        "\\input{generated/bibliography}" in w.message and "IEEEtran" in w.message
        for w in result.warnings
    )


def test_migrated_main_tex_does_not_warn(tmp_path):
    # A main.tex already using the new include must NOT trigger the migration
    # warning on subsequent runs.
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    output_root = tmp_path / "output"
    emit_project(docx, "ieeetran", output_root)  # writes new-style main.tex

    result = emit_project(docx, "ieeetran", output_root)  # second run
    assert result.main_tex_written is False
    assert not any("bibliography" in w.message for w in result.warnings)


# --------------------------------------------------------------------------- #
# Figure edge cases + robust output paths
# --------------------------------------------------------------------------- #


def test_duplicate_figure_numbers_warn_instead_of_silently_collapsing(tmp_path):
    # Two Figure records sharing a number would both copy to figures/fig2.* and
    # the number->path map would keep only one -- a silent lost figure. The
    # emitter must surface a warning rather than drop it without a trace.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.png").write_bytes(b"AAA")
    (src / "b.png").write_bytes(b"BBB")
    figures = (
        Figure(number=2, caption="first", embedded_path=src / "a.png"),
        Figure(number=2, caption="second", embedded_path=src / "b.png"),
    )
    figures_dir = tmp_path / "out_figs"
    figures_dir.mkdir()

    _files, _updated, warnings = _copy_figures(figures, figures_dir)

    assert any("figure number 2" in w.message and "duplicate" in w.message for w in warnings)


def test_output_path_with_spaces_and_unicode(tmp_path):
    # Emit into an output tree whose path has spaces and non-ASCII characters.
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    weird_root = tmp_path / "out dir with spaces 中文 é"

    result = emit_project(docx, "revtex4-2", weird_root)

    assert result.output_dir.is_dir()
    assert result.main_tex_path.is_file()
    assert result.report_path is not None and result.report_path.is_file()


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_citation_free_manuscript_compiles_under_ieeetran(tmp_path):
    # Plan item 26 core done-when: a citation-free manuscript targeting IEEE
    # compiles to a real PDF (previously failed with "Something's wrong --
    # perhaps a missing \item" at \end{thebibliography}).
    from latextify.compile.tectonic import compile_document, ensure_tectonic

    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = emit_project(docx, "ieeetran", tmp_path / "output", report=False)

    compile_result = compile_document(result.main_tex_path, tectonic_path=ensure_tectonic())
    assert compile_result.success, compile_result.raw_log
    assert compile_result.pdf_path is not None
    assert compile_result.pdf_path.is_file()
    assert compile_result.pdf_path.stat().st_size > 0


# --------------------------------------------------------------------------- #
# Online reference validation wiring (opt-in --check-references)
# --------------------------------------------------------------------------- #


def test_check_references_off_by_default(tmp_path):
    # Default emit does NO online validation: result.validation is None and the
    # report says so (never touches the network).
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    assert result.validation is None
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Reference Validation\n_Not checked_" in report


def test_check_references_attaches_report_and_renders(tmp_path, monkeypatch):
    # With check_references=True the emitter validates the FINAL entry set and
    # folds the outcome into EmitResult.validation and report.md. The Crossref
    # round-trip is stubbed here (the validation logic itself is unit-tested in
    # test_citations_validate.py against a mock transport).
    from latextify.emit import project as project_mod
    from latextify.model.validate import ValidationRecord, ValidationReport

    captured: dict[str, object] = {}

    def fake_validate(entries, client, **kwargs):
        captured["entries"] = entries
        return ValidationReport(
            records=tuple(
                ValidationRecord(key=e.key, status="verified", doi=e.doi) for e in entries
            )
            + (ValidationRecord(key="planted", status="dead_doi", doi="10.9/x"),)
        )

    monkeypatch.setattr(project_mod, "validate_references", fake_validate)

    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output", check_references=True)

    # The real, keyed entries reached the validator (not an empty list).
    assert captured["entries"], "validator should receive the assembled entries"
    assert result.validation is not None
    assert result.validation.count("dead_doi") == 1

    report = result.report_path.read_text(encoding="utf-8")
    assert "## Reference Validation" in report
    assert "Checked" in report and "against Crossref" in report
    assert "`planted`" in report  # the flagged record is listed


def test_check_references_partial_outage_warns(tmp_path, monkeypatch):
    # Some (not all) references unchecked mid-run -> one visible warning so the
    # author knows the check partially degraded; the emit itself still succeeds.
    from latextify.emit import project as project_mod
    from latextify.model.validate import ValidationRecord, ValidationReport

    def fake_validate(entries, client, **kwargs):
        records = [ValidationRecord(key=e.key, status="verified", doi=e.doi) for e in entries]
        records[0] = ValidationRecord(key=records[0].key, status="unchecked")
        return ValidationReport(records=tuple(records))

    monkeypatch.setattr(project_mod, "validate_references", fake_validate)

    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output", check_references=True)

    assert result.validation is not None
    assert any("could not be checked" in w.message for w in result.warnings)


def test_check_references_skipped_when_no_entries(tmp_path, monkeypatch):
    # A citation-free manuscript has no entries, so validation is skipped
    # entirely (no client built, no network) even when requested.
    from latextify.emit import project as project_mod

    def exploding_validate(entries, client, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("validation must not run with an empty entry set")

    monkeypatch.setattr(project_mod, "validate_references", exploding_validate)

    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output", check_references=True)

    assert result.validation is None


# --------------------------------------------------------------------------- #
# Stale generated figure reconciliation (audit item 7)
# --------------------------------------------------------------------------- #


def _touch(dirp: Path, name: str) -> Path:
    dirp.mkdir(parents=True, exist_ok=True)
    p = dirp / name
    p.write_bytes(b"x")
    return p


def test_prune_removes_only_unkept_owned_main_figures(tmp_path):
    figs = tmp_path / "figures"
    _touch(figs, "fig1.pdf")   # current
    _touch(figs, "fig2.png")   # stale (fewer figures now)
    _prune_stale_figures(figs, "", {"fig1.pdf"})
    assert (figs / "fig1.pdf").exists()
    assert not (figs / "fig2.png").exists()


def test_prune_handles_format_change(tmp_path):
    figs = tmp_path / "figures"
    _touch(figs, "fig1.png")   # last run's raster
    _prune_stale_figures(figs, "", {"fig1.pdf"})  # now a PDF
    assert not (figs / "fig1.png").exists()


def test_prune_preserves_user_files_and_sibling_document(tmp_path):
    figs = tmp_path / "figures"
    _touch(figs, "fig1.pdf")        # current main
    _touch(figs, "Fig1.png")        # user file (capital F) -- must survive
    _touch(figs, "diagram.pdf")     # user file -- must survive
    _touch(figs, "figS1.pdf")       # supplement's figure -- main pass must NOT touch
    _prune_stale_figures(figs, "", {"fig1.pdf"})
    assert (figs / "Fig1.png").exists()
    assert (figs / "diagram.pdf").exists()
    assert (figs / "figS1.pdf").exists()


def test_prune_supplement_prefix_leaves_main_alone(tmp_path):
    figs = tmp_path / "figures"
    _touch(figs, "fig1.pdf")        # main figure
    _touch(figs, "figS1.pdf")       # current supplement
    _touch(figs, "figS2.pdf")       # stale supplement
    _prune_stale_figures(figs, "S", {"figS1.pdf"})
    assert (figs / "fig1.pdf").exists()      # untouched by supplement pass
    assert (figs / "figS1.pdf").exists()
    assert not (figs / "figS2.pdf").exists()


def test_prune_zero_figures_clears_all_owned(tmp_path):
    figs = tmp_path / "figures"
    _touch(figs, "fig1.pdf")
    _touch(figs, "fig2.pdf")
    _touch(figs, "keep.txt")
    _prune_stale_figures(figs, "", set())  # a run with no figures
    assert not (figs / "fig1.pdf").exists()
    assert not (figs / "fig2.pdf").exists()
    assert (figs / "keep.txt").exists()


def test_emit_rerun_removes_stale_generated_figures(tmp_path):
    # Full pipeline: emit once (writes fig1..figN), drop a stale generated figure
    # and a user file into figures/, emit again -> stale gone, user file kept,
    # current figures present.
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    output_root = tmp_path / "output"
    first = emit_project(docx, "revtex4-2", output_root)
    figs_dir = first.figures_dir
    current = {p.name for p in figs_dir.glob("fig*.*")}
    assert current, "expected generated figures on the first run"

    stale = _touch(figs_dir, "fig99.png")   # generated-looking, not produced now
    user = _touch(figs_dir, "my_photo.jpg")  # user-owned

    emit_project(docx, "revtex4-2", output_root)
    assert not stale.exists(), "stale generated figure should be pruned on re-run"
    assert user.exists(), "user file must be preserved"
    for name in current:
        assert (figs_dir / name).exists(), f"current figure {name} must remain"
