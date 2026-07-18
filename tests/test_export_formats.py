"""Tests for latextify.emit.alt_formats -- HTML/Markdown export (items 4-5).

Copies each fixture docx into tmp_path first -- see tests/test_emit.py's
module docstring for why (load_or_create_meta writes a write-once
paper.yaml sidecar beside whatever docx path it's given).
"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from latextify.citations.crossref import CrossrefClient
from latextify.emit.alt_formats import export_html, export_markdown
from latextify.emit.project import emit_project

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN_DOCX = FIXTURES / "clean.docx"
FIGURES_DOCX = FIXTURES / "figures.docx"
EQUATIONS_DOCX = FIXTURES / "equations.docx"
ZOTERO_DOCX = FIXTURES / "zotero_cited.docx"
HAND_CITED_DOCX = FIXTURES / "hand_cited.docx"
BRACKET_CITED_DOCX = FIXTURES / "bracket_cited.docx"

# A distinctive phrase from bracket_cited.docx's reference #1 -- see
# tests/fixtures/make_bracket_cited.py.
_BRACKET_REF_1_PHRASE = "Foundational widget calibration techniques"

_MARKDOWN_REFERENCES_HEADING_RE = re.compile(r"^#{1,6}[ \t]+References[ \t]*$", re.MULTILINE)
_HTML_REFERENCES_HEADING_RE = re.compile(r"<h[1-6][^>]*>References</h[1-6]>")


def _mock_crossref_no_match(**_kwargs: object) -> CrossrefClient:
    """A ``CrossrefClient`` that matches nothing (no network, fully offline).

    Used for the plaintext-citation-path tests below: every typed reference
    falls back to a keyless "raw" entry (``reconcile.raw_refentry``) built
    verbatim from its typed text -- deterministic and fast, and exactly what
    is needed to exercise the duplicate-reference-list fix without depending
    on Crossref actually matching (or being reachable).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"items": []}})

    return CrossrefClient(mailto="test@example.com", transport=httpx.MockTransport(handler))


def _copy_fixture(tmp_path: Path, src: Path) -> Path:
    dest = tmp_path / src.name
    shutil.copy(src, dest)
    return dest


def _parse_xhtml(html_text: str) -> ET.Element:
    """Confirm ``html_text`` opens/parses as well-formed XHTML.

    pandoc's ``--standalone`` HTML output is XHTML-flavored (self-closing
    ``<meta ... />``/``<img ... />``); stripping the ``<!DOCTYPE html>``
    prologue and the ``xmlns`` attribute noise via ``ET.fromstring`` is
    enough to confirm the document is well-formed markup, not just a string
    that happens to contain HTML-looking substrings.
    """
    body = html_text.split("\n", 1)[1] if html_text.startswith("<!DOCTYPE") else html_text
    return ET.fromstring(body)


# --------------------------------------------------------------------------- #
# Fixtures exist
# --------------------------------------------------------------------------- #


def test_fixtures_exist():
    fixtures = (
        CLEAN_DOCX,
        FIGURES_DOCX,
        EQUATIONS_DOCX,
        ZOTERO_DOCX,
        HAND_CITED_DOCX,
        BRACKET_CITED_DOCX,
    )
    for fixture in fixtures:
        assert fixture.is_file(), f"tests/fixtures/{fixture.name} is missing"


# --------------------------------------------------------------------------- #
# Markdown export
# --------------------------------------------------------------------------- #


def test_export_markdown_contains_body_prose(tmp_path):
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = export_markdown(docx, tmp_path / "clean.md")

    assert result.output_path.is_file()
    text = result.output_path.read_text(encoding="utf-8")
    assert "This manuscript uses only plain paragraphs" in text
    assert "More ordinary body text, spanning a second paragraph." in text


def test_export_markdown_contains_literal_dollar_math(tmp_path):
    docx = _copy_fixture(tmp_path, EQUATIONS_DOCX)
    result = export_markdown(docx, tmp_path / "equations.md")

    text = result.output_path.read_text(encoding="utf-8")
    assert r"\frac{a}{b}" in text
    # Inline math: single-dollar delimiters; display math: double-dollar.
    assert "$\\frac{a}{b}$" in text
    assert "$$\\frac{a}{b}$$" in text


def test_export_markdown_contains_reference_list(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = export_markdown(docx, tmp_path / "zotero.md")

    text = result.output_path.read_text(encoding="utf-8")
    assert "## References" in text
    assert result.citation_count == 5
    # Numbered in-text markers resolved from the ZZLTXCITE sentinels.
    assert "[1]" in text


def test_export_markdown_plaintext_citation_path_still_yields_reference_list(tmp_path):
    """hand_cited.docx has no citation field codes -- item 14's plain-text
    reconstruction fallback still produces a reference list (the module's
    documented simplification: in-text markers are left as typed, and a
    warning is emitted -- see latextify.emit.alt_formats's module docstring).
    """
    docx = _copy_fixture(tmp_path, HAND_CITED_DOCX)
    result = export_markdown(docx, tmp_path / "hand_cited.md")

    text = result.output_path.read_text(encoding="utf-8")
    assert "## References" in text
    assert result.citation_count > 0
    assert any("no citation field codes found" in w.message for w in result.warnings)


def test_export_markdown_figures_copied_and_referenced(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    output = tmp_path / "figures.md"
    result = export_markdown(docx, output)

    assert result.figure_count == 3
    text = output.read_text(encoding="utf-8")
    media_dir = tmp_path / "figures_files"
    assert media_dir.is_dir()
    assert (media_dir / "fig1.png").is_file()
    assert (
        "![A red placeholder figure, captioned via Word's Caption style.]"
        "(figures_files/fig1.png)" in text
    )
    # The leftover "Figure N:"/"Fig. N:" caption paragraph pandoc left as a
    # separate sibling block must not survive as duplicate plain text.
    assert "Figure 2:" not in text
    assert "Fig. 3:" not in text


# --------------------------------------------------------------------------- #
# HTML export
# --------------------------------------------------------------------------- #


def test_export_html_is_self_contained_with_embedded_figures(tmp_path):
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = export_html(docx, tmp_path / "figures.html")

    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)  # opens/parses as well-formed XHTML

    assert result.figure_count == 3
    assert text.count("data:image/png;base64,") == 3
    # No external network reference: every src=/href= is either a data: URI,
    # an in-page "#..." anchor, or absent (no <link>/<script> to a CDN).
    for attr in ("src=", "href="):
        start = 0
        while True:
            idx = text.find(attr, start)
            if idx == -1:
                break
            value_start = idx + len(attr) + 1  # skip the opening quote
            value = text[value_start : text.find('"', value_start)]
            assert value.startswith("data:") or value.startswith("#"), (
                f"non-self-contained {attr}{value!r} found in HTML export"
            )
            start = idx + 1


def test_export_html_mathml_present(tmp_path):
    docx = _copy_fixture(tmp_path, EQUATIONS_DOCX)
    result = export_html(docx, tmp_path / "equations.html")

    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)
    assert "<math " in text
    assert 'xmlns="http://www.w3.org/1998/Math/MathML"' in text


def test_export_html_reference_list_and_linked_citations(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = export_html(docx, tmp_path / "zotero.html")

    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)
    assert '<section id="references">' in text
    assert result.citation_count == 5
    assert '<a href="#ref-1">1</a>' in text
    assert 'id="ref-1"' in text


# --------------------------------------------------------------------------- #
# Plaintext-citation path: the typed reference list must not survive
# alongside the reconstructed one (the duplicate-reference-list fix)
# --------------------------------------------------------------------------- #


def test_export_markdown_plaintext_citation_path_has_exactly_one_reference_list(
    tmp_path, monkeypatch
):
    """bracket_cited.docx's own typed "References" section must be stripped
    from the AST -- pre-fix, it survived alongside the reconstructed list
    below, so "References" appeared as a heading twice.
    """
    monkeypatch.setattr("latextify.citations.crossref.CrossrefClient", _mock_crossref_no_match)
    docx = _copy_fixture(tmp_path, BRACKET_CITED_DOCX)
    result = export_markdown(docx, tmp_path / "bracket_cited.md")

    text = result.output_path.read_text(encoding="utf-8")
    assert len(_MARKDOWN_REFERENCES_HEADING_RE.findall(text)) == 1
    # The reconstructed entry appears once -- not once in the typed list and
    # once more in the reconstructed one.
    assert text.count(_BRACKET_REF_1_PHRASE) == 1
    assert result.citation_count == 5


def test_export_markdown_plaintext_citation_path_in_text_marker_aligns(tmp_path, monkeypatch):
    """The "[2]" in-text marker is left as typed (see the module docstring's
    documented simplification) but its number must still fall inside the
    reconstructed list's 1..N range -- reconstruct_citations numbers typed
    entries in the same order the author's own "[N]" markers assume, so the
    strip must not have shifted that numbering.
    """
    monkeypatch.setattr("latextify.citations.crossref.CrossrefClient", _mock_crossref_no_match)
    docx = _copy_fixture(tmp_path, BRACKET_CITED_DOCX)
    result = export_markdown(docx, tmp_path / "bracket_cited.md")

    text = result.output_path.read_text(encoding="utf-8")
    # pandoc's markdown writer backslash-escapes literal "[" / "]".
    assert "\\[2\\]" in text or "[2]" in text
    assert 1 <= 2 <= result.citation_count


def test_export_html_plaintext_citation_path_has_exactly_one_reference_list(tmp_path, monkeypatch):
    monkeypatch.setattr("latextify.citations.crossref.CrossrefClient", _mock_crossref_no_match)
    docx = _copy_fixture(tmp_path, BRACKET_CITED_DOCX)
    result = export_html(docx, tmp_path / "bracket_cited.html")

    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)
    assert len(_HTML_REFERENCES_HEADING_RE.findall(text)) == 1
    assert text.count(_BRACKET_REF_1_PHRASE) == 1
    assert result.citation_count == 5


def test_export_html_plaintext_citation_path_in_text_marker_aligns(tmp_path, monkeypatch):
    monkeypatch.setattr("latextify.citations.crossref.CrossrefClient", _mock_crossref_no_match)
    docx = _copy_fixture(tmp_path, BRACKET_CITED_DOCX)
    result = export_html(docx, tmp_path / "bracket_cited.html")

    text = result.output_path.read_text(encoding="utf-8")
    assert "[2]" in text  # HTML does not escape literal brackets
    assert 1 <= 2 <= result.citation_count


def test_export_markdown_plaintext_citation_path_warning_is_accurate(tmp_path, monkeypatch):
    """The warning must no longer claim a duplicate list survives (it doesn't,
    after the fix) -- only that in-text markers are left unlinked.
    """
    monkeypatch.setattr("latextify.citations.crossref.CrossrefClient", _mock_crossref_no_match)
    docx = _copy_fixture(tmp_path, BRACKET_CITED_DOCX)
    result = export_markdown(docx, tmp_path / "bracket_cited.md")

    messages = [w.message for w in result.warnings]
    assert any("no citation field codes found" in m for m in messages)
    # The old (pre-fix) wording claimed the typed list was left in the body,
    # producing a duplicate -- no longer true, so it must be gone.
    assert not any("was not removed from the body" in m for m in messages)
    assert not any("the typed reference list" in m and "not stripped" in m for m in messages)


def test_export_markdown_field_coded_path_unaffected_by_reference_strip(tmp_path):
    """Regression: zotero_cited.docx (field-coded citations) must be
    completely untouched by the AST strip -- it only fires on the plaintext
    path (see ``_prepare_reference_data``'s ``strip_typed_list`` flag).
    """
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = export_markdown(docx, tmp_path / "zotero.md")

    text = result.output_path.read_text(encoding="utf-8")
    assert len(_MARKDOWN_REFERENCES_HEADING_RE.findall(text)) == 1
    assert result.citation_count == 5
    assert not any("no citation field codes found" in w.message for w in result.warnings)


def test_export_html_field_coded_path_unaffected_by_reference_strip(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = export_html(docx, tmp_path / "zotero.html")

    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)
    assert len(_HTML_REFERENCES_HEADING_RE.findall(text)) == 1
    assert '<a href="#ref-1">1</a>' in text
    assert not any("no citation field codes found" in w.message for w in result.warnings)


def test_export_markdown_no_citation_path_no_spurious_reference_list(tmp_path):
    """Regression: clean.docx has no citations at all -- must export cleanly
    with no reference list (empty, not a spurious "## References" with
    nothing under it), and the AST strip must not fire (no typed reference
    list was ever reconstructed to strip).
    """
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = export_markdown(docx, tmp_path / "clean.md")

    assert result.output_path.is_file()
    text = result.output_path.read_text(encoding="utf-8")
    assert not _MARKDOWN_REFERENCES_HEADING_RE.search(text)
    assert result.citation_count == 0
    assert result.warnings == ()


def test_export_html_no_citation_path_no_spurious_reference_list(tmp_path):
    docx = _copy_fixture(tmp_path, CLEAN_DOCX)
    result = export_html(docx, tmp_path / "clean.html")

    assert result.output_path.is_file()
    text = result.output_path.read_text(encoding="utf-8")
    _parse_xhtml(text)
    assert not _HTML_REFERENCES_HEADING_RE.search(text)
    assert result.citation_count == 0
    assert result.warnings == ()


# --------------------------------------------------------------------------- #
# Regression: the LaTeX path is unchanged by the convert_docx_to_ast refactor
# --------------------------------------------------------------------------- #


def test_emit_project_latex_output_unchanged_on_figures_docx(tmp_path):
    """Same assertions as test_emit.py's wrapped/bare figure-anchor tests --
    proves the ingest.pandoc/ingest.filters refactor (convert_docx_to_ast +
    apply_shared, extracted so alt_formats can reuse the shared AST-reading
    half) left the LaTeX body pipeline byte-identical.
    """
    docx = _copy_fixture(tmp_path, FIGURES_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert body.count("\\begin{figure}") == 3
    assert body.count("\\end{figure}") == 3
    assert "\\includegraphics[width=\\linewidth]{figures/fig1.png}" in body
    assert "\\caption{A red placeholder figure, captioned via Word's Caption style.}" in body
    assert "Figure 1:" not in body
    assert "\\includegraphics[width=\\linewidth]{figures/fig2.png}" in body
    assert "\\caption{A green placeholder figure, captioned via a plain paragraph.}" in body
    assert "\\includegraphics[width=\\linewidth]{figures/fig3.png}" in body
    assert "\\caption{A blue placeholder figure, captioned with the abbreviated label.}" in body
    assert "Figure 2:" not in body
    assert "Fig. 3:" not in body


def test_emit_project_latex_citations_unchanged_on_zotero_docx(tmp_path):
    docx = _copy_fixture(tmp_path, ZOTERO_DOCX)
    result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = result.body_tex_path.read_text(encoding="utf-8")

    # zotero_cited.docx has 4 in-text citation occurrences (one, "[2,3]",
    # resolves to 2 of the 5 total references.bib entries) -- EmitResult
    # .citation_count counts IN-TEXT occurrences, unlike ExportResult
    # .citation_count above, which counts reference-list ENTRIES; the two
    # are deliberately different metrics, not a regression.
    assert result.citation_count == 4
    assert body.count("\\cite{") == 4
    assert "ZZLTXCITE" not in body
    assert "%%CITE" not in body
