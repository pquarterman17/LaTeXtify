"""Tests for .odt/.rtf/.md manuscript support (GUI_OPTIONS_FORMATS_PLAN item 9).

Fixtures are generated at runtime (no binaries committed): a .md fixture is a
written string, .rtf likewise (minimal RTF), and .odt is generated with
pypandoc from a markdown string -- skipped gracefully if generation fails
(e.g. no pandoc binary available in a stripped-down environment).

Covers, per the plan item: pandoc format routing, and each docx-specific
stage's degradation --

    preflight    -- empty report, no crash (latextify.ingest.preflight)
    metadata     -- weak guess + written sidecar; an existing sidecar wins
    citations    -- extract_field_citations empty -> plain-text path takes
                    over, and typed reference lists DO get reconstructed
    figures      -- extract_figures degrades to no figures where pandoc's
                    reader can't find any (never crashes)

plus the GUI/CLI accept-list (widened extension checks live in test_gui.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latextify.citations.fields import extract_field_citations
from latextify.emit.project import emit_project
from latextify.ingest import formats
from latextify.ingest.preflight import run_preflight

FIXTURE_MD = """# A Fake Test Manuscript Title

This is the introduction. Prior results [1] showed something interesting
about test physics.

## References

[1] Doe, J. Example Title About Test Physics. Journal of Testing 12, 345 (2020).
"""

#: No "References" heading/typed citation marker -- the .odt test only needs
#: to exercise format routing + text conversion, and must never trigger the
#: plain-text path's Crossref fallback (this repo's tests run offline).
FIXTURE_MD_NO_REFS = "# An Odt Fixture Title\n\nBody text mentioning something interesting.\n"

FIXTURE_BIB = (
    "@article{doe2020test, title={Example Title About Test Physics}, "
    "author={Doe, J.}, journal={Journal of Testing}, year={2020}, "
    "doi={10.9999/test.2020.001}}\n"
)

_MINIMAL_RTF = (
    r"{\rtf1\ansi Minimal RTF Manuscript\par "
    r"This body paragraph mentions a quokka for the RTF fidelity test.\par}"
)


def _write_md(path: Path, text: str = FIXTURE_MD) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _write_rtf(path: Path, text: str = _MINIMAL_RTF) -> Path:
    path.write_text(text, encoding="ascii")
    return path


def _write_odt(path: Path, md_text: str = FIXTURE_MD) -> Path | None:
    """Generate an .odt from ``md_text`` via pypandoc; ``None`` if that fails."""
    try:
        import pypandoc

        pypandoc.convert_text(md_text, to="odt", format="md", outputfile=str(path))
    except Exception:
        return None
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# latextify.ingest.formats
# --------------------------------------------------------------------------- #


def test_pandoc_format_for_recognized_extensions():
    assert formats.pandoc_format_for("paper.docx") == "docx"
    assert formats.pandoc_format_for("paper.odt") == "odt"
    assert formats.pandoc_format_for("paper.rtf") == "rtf"
    assert formats.pandoc_format_for("paper.md") == "markdown"
    assert formats.pandoc_format_for("PAPER.MD") == "markdown"  # case-insensitive


def test_pandoc_format_for_unrecognized_extension_raises():
    with pytest.raises(ValueError, match="unrecognized manuscript file type"):
        formats.pandoc_format_for("paper.txt")


def test_is_docx_and_is_alt_manuscript_format():
    assert formats.is_docx("paper.docx") is True
    assert formats.is_docx("paper.odt") is False
    for ext in ("odt", "rtf", "md"):
        assert formats.is_alt_manuscript_format(f"paper.{ext}") is True
    assert formats.is_alt_manuscript_format("paper.docx") is False
    assert formats.is_alt_manuscript_format("paper.zip") is False  # bogus, not recognized


def test_non_docx_warnings_empty_for_docx():
    assert formats.non_docx_warnings("paper.docx", sidecar_existed=True) == []
    assert formats.non_docx_warnings("paper.docx", sidecar_existed=False) == []


def test_non_docx_warnings_flags_missing_sidecar():
    with_sidecar = formats.non_docx_warnings("paper.md", sidecar_existed=True)
    without_sidecar = formats.non_docx_warnings("paper.md", sidecar_existed=False)
    assert len(with_sidecar) == 1
    assert len(without_sidecar) == 2
    assert "paper.yaml" in without_sidecar[1]


# --------------------------------------------------------------------------- #
# preflight degradation (stage 2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ext", ["odt", "rtf", "md"])
def test_run_preflight_degrades_to_empty_report_for_alt_formats(tmp_path, ext):
    path = tmp_path / f"manuscript.{ext}"
    path.write_text("placeholder", encoding="utf-8")  # content is irrelevant here
    report = run_preflight(path)
    assert report.findings == ()
    assert report.has_errors is False
    assert report.styles.heading_levels_used == frozenset()


# --------------------------------------------------------------------------- #
# field-code citation extraction degradation (stage 4)
# --------------------------------------------------------------------------- #


def test_extract_field_citations_empty_for_alt_formats(tmp_path):
    path = _write_md(tmp_path / "manuscript.md")
    result = extract_field_citations(path)
    assert result.entries == []
    assert result.citations == []


# --------------------------------------------------------------------------- #
# end-to-end emit_project per format
# --------------------------------------------------------------------------- #


def _assert_project_shape(result, expected_body_text: str) -> None:
    assert result.main_tex_path.is_file()
    assert result.preamble_tex_path.is_file()
    assert result.body_tex_path.is_file()
    # pandoc's LaTeX writer wraps long lines, so a phrase spanning a wrap
    # point would otherwise contain a literal newline -- normalize whitespace
    # before the containment check (same flattening report/render.py uses).
    body = " ".join(result.body_tex_path.read_text(encoding="utf-8").split())
    assert expected_body_text in body


def test_emit_project_from_markdown_end_to_end(tmp_path):
    md_path = _write_md(tmp_path / "manuscript.md")
    bib_path = tmp_path / "refs.bib"
    bib_path.write_text(FIXTURE_BIB, encoding="utf-8")

    result = emit_project(
        md_path, "revtex4-2", tmp_path / "output", references_bib_path=bib_path
    )

    _assert_project_shape(result, "something interesting")
    # Typed reference list reconstruction (stage 4's fallback path) actually ran:
    # the offline .bib match resolved the [1] marker to a real \cite{}, not an
    # unresolved-citation placeholder.
    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\cite{doe2020example}" in body
    assert "UNRESOLVED CITATION" not in body
    assert [e.key for e in result.entries] == ["doe2020example"]

    # Report warnings mention the degraded docx-only stages.
    messages = " ".join(w.message for w in result.warnings)
    assert "not .docx" in messages
    assert "preflight" in messages
    assert "no paper.yaml sidecar found" in messages

    # paper.yaml sidecar written once, with the guessed (weak) metadata.
    sidecar = md_path.with_name("paper.yaml")
    assert sidecar.is_file()
    assert "Unknown Author" in sidecar.read_text(encoding="utf-8")


def test_emit_project_from_rtf_end_to_end(tmp_path):
    rtf_path = _write_rtf(tmp_path / "manuscript.rtf")

    result = emit_project(rtf_path, "revtex4-2", tmp_path / "output")

    _assert_project_shape(result, "quokka")
    messages = " ".join(w.message for w in result.warnings)
    assert "not .docx" in messages


def test_emit_project_from_odt_end_to_end(tmp_path):
    odt_path = _write_odt(tmp_path / "manuscript.odt", FIXTURE_MD_NO_REFS)
    if odt_path is None:
        pytest.skip("pypandoc could not generate an .odt fixture in this environment")

    result = emit_project(odt_path, "revtex4-2", tmp_path / "output")

    _assert_project_shape(result, "something interesting")
    assert result.entries == ()  # no reference list in the fixture -- no network touched


def test_emit_project_respects_existing_paper_yaml_sidecar_for_alt_format(tmp_path):
    """A hand-written/pre-existing paper.yaml beats the weak non-docx guess."""
    md_path = _write_md(tmp_path / "manuscript.md", "# Placeholder\n\nBody.\n")
    sidecar = md_path.with_name("paper.yaml")
    sidecar.write_text(
        "title: A Real Handwritten Title\n"
        "authors:\n"
        "  - name: Jane Q. Researcher\n"
        "affiliations: []\n"
        "abstract: ''\n"
        "keywords: []\n",
        encoding="utf-8",
    )

    result = emit_project(md_path, "revtex4-2", tmp_path / "output")

    metadata_tex = result.metadata_tex_path.read_text(encoding="utf-8")
    assert "Real Handwritten Title" in metadata_tex
    assert "Unknown Author" not in metadata_tex
    # The sidecar already existed -- no "guessed weakly" warning this run.
    messages = " ".join(w.message for w in result.warnings)
    assert "no paper.yaml sidecar found" not in messages


def test_emit_project_embeds_a_markdown_referenced_figure(tmp_path):
    """pandoc's own reader extracts a markdown manuscript's referenced image
    (via --resource-path so a path relative to the manuscript resolves) --
    the figures/fig<N>.<ext> drop-in override convention still layers on top
    of whatever this stage does manage to extract."""
    pil_image = pytest.importorskip("PIL.Image")
    img_path = tmp_path / "dot.png"
    pil_image.new("RGB", (6, 4), color="red").save(img_path)

    md_path = _write_md(
        tmp_path / "manuscript.md",
        "# Figure Manuscript\n\n![A red rectangle](dot.png)\n\nSee the figure above.\n",
    )

    result = emit_project(md_path, "revtex4-2", tmp_path / "output")

    assert result.figure_count == 1
    assert (result.figures_dir / "fig1.png").is_file()
    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\includegraphics" in body


# --------------------------------------------------------------------------- #
# supplement role (also accepts .odt/.rtf/.md)
# --------------------------------------------------------------------------- #


def test_emit_project_accepts_a_non_docx_supplement(tmp_path):
    main_path = _write_md(tmp_path / "main.md", "# Main Document\n\nMain body text.\n")
    supplement_path = _write_rtf(tmp_path / "supplement.rtf")

    result = emit_project(
        main_path, "revtex4-2", tmp_path / "output", supplement_docx_path=supplement_path
    )

    assert result.supplement is not None
    assert result.supplement.supplement_tex_written is True
    supplement_body = (
        result.output_dir / "generated" / "supplement_body.tex"
    ).read_text(encoding="utf-8")
    assert "quokka" in supplement_body
