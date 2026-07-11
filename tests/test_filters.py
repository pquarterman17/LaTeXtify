"""Unit tests for latextify.ingest.filters against synthetic panflute ASTs.

These don't need pandoc/docx at all for the AST-level assertions (they build
panflute.Doc objects directly), except for test_anchors_survive_latex_emission
which also runs the resulting Doc through pandoc's real json->latex writer to
prove the raw anchors survive unescaped in emitted LaTeX text -- the actual
codepath latextify.ingest.pandoc uses.
"""

import io

import panflute as pf
import pypandoc

from latextify.ingest.filters import (
    normalize_headings,
    plant_anchors,
    strip_word_junk,
)


def _headers(doc: pf.Doc) -> list[int]:
    levels = []
    doc.walk(lambda elem, doc: levels.append(elem.level) if isinstance(elem, pf.Header) else None)
    return levels


# ---------------------------
# normalize_headings
# ---------------------------


def test_headings_1_2_3_pass_through_unchanged():
    doc = pf.Doc(
        pf.Header(pf.Str("A"), level=1),
        pf.Header(pf.Str("B"), level=2),
        pf.Header(pf.Str("C"), level=3),
    )
    doc, findings = normalize_headings(doc)
    assert _headers(doc) == [1, 2, 3]
    assert findings == []


def test_headings_starting_above_1_are_shifted_down():
    # A manuscript that reserves level 1 for the title and starts body
    # sections at "Heading 2" should have its levels shifted so the
    # shallowest heading becomes \section (level 1).
    doc = pf.Doc(
        pf.Header(pf.Str("Section"), level=2),
        pf.Header(pf.Str("Subsection"), level=3),
    )
    doc, findings = normalize_headings(doc)
    assert _headers(doc) == [1, 2]
    assert findings == []


def test_headings_deeper_than_3_are_clamped_with_a_finding():
    doc = pf.Doc(
        pf.Header(pf.Str("A"), level=1),
        pf.Header(pf.Str("B"), level=4),
        pf.Header(pf.Str("C"), level=6),
    )
    doc, findings = normalize_headings(doc)
    assert _headers(doc) == [1, 3, 3]
    assert len(findings) == 2
    assert "heading level 4" in findings[0].message
    assert "clamped to 3" in findings[0].message
    assert "heading level 6" in findings[1].message


def test_normalize_headings_no_op_on_doc_without_headers():
    doc = pf.Doc(pf.Para(pf.Str("no headers here")))
    doc, findings = normalize_headings(doc)
    assert findings == []
    assert isinstance(doc, pf.Doc)


# ---------------------------
# strip_word_junk
# ---------------------------


def test_strip_word_junk_removes_empty_span_but_keeps_surrounding_text():
    para = pf.Para(
        pf.Str("Hello"),
        pf.Space,
        pf.Span(identifier="bookmark123"),  # empty content -> junk
        pf.Str("World"),
    )
    doc = pf.Doc(para)
    doc = strip_word_junk(doc)

    kept = doc.content[0].content
    assert [type(e) for e in kept] == [pf.Str, pf.Space, pf.Str]
    assert [e.text for e in kept if isinstance(e, pf.Str)] == ["Hello", "World"]


def test_strip_word_junk_removes_empty_div_but_keeps_other_blocks():
    doc = pf.Doc(
        pf.Para(pf.Str("before")),
        pf.Div(),  # empty content -> junk
        pf.Para(pf.Str("after")),
    )
    doc = strip_word_junk(doc)
    assert [type(b) for b in doc.content] == [pf.Para, pf.Para]


def test_strip_word_junk_removes_empty_str_runs():
    para = pf.Para(pf.Str("Hello"), pf.Str(""), pf.Str("World"))
    doc = pf.Doc(para)
    doc = strip_word_junk(doc)
    assert [s.text for s in doc.content[0].content] == ["Hello", "World"]


def test_strip_word_junk_preserves_non_empty_span_and_div():
    doc = pf.Doc(
        pf.Para(pf.Span(pf.Str("kept"), identifier="real")),
        pf.Div(pf.Para(pf.Str("also kept")), identifier="real-div"),
    )
    doc = strip_word_junk(doc)
    assert len(doc.content) == 2
    assert isinstance(doc.content[0].content[0], pf.Span)
    assert isinstance(doc.content[1], pf.Div)


# ---------------------------
# plant_anchors
# ---------------------------


def test_plant_anchors_numbers_figures_and_citations_in_document_order():
    doc = pf.Doc(
        pf.Para(pf.Str("intro")),
        pf.Para(pf.Image(pf.Str("alt1"), url="media/image1.png")),
        pf.Para(pf.Cite(pf.Str("[1]"), citations=[pf.Citation(id="smith2020")])),
        pf.Para(pf.Image(pf.Str("alt2"), url="media/image2.png")),
        pf.Para(pf.Cite(pf.Str("[2]"), citations=[pf.Citation(id="doe2021")])),
    )
    doc, counts = plant_anchors(doc)

    assert counts.figures == 2
    assert counts.citations == 2

    raw_texts: list[str] = []
    doc.walk(
        lambda elem, doc: raw_texts.append(elem.text)
        if isinstance(elem, pf.RawInline)
        else None
    )
    assert raw_texts == ["%%FIGURE:1%%", "%%CITE:1%%", "%%FIGURE:2%%", "%%CITE:2%%"]


def test_plant_anchors_emits_raw_latex_format_not_str():
    # Must be RawInline(format="latex"), not Str -- pandoc's LaTeX writer
    # escapes "%" in Str content, which would corrupt the anchor marker.
    doc = pf.Doc(pf.Para(pf.Image(pf.Str("alt"), url="img.png")))
    doc, counts = plant_anchors(doc)
    assert counts.figures == 1

    anchors = []
    doc.walk(lambda elem, doc: anchors.append(elem) if isinstance(elem, pf.RawInline) else None)
    assert len(anchors) == 1
    assert anchors[0].format == "latex"
    assert anchors[0].text == "%%FIGURE:1%%"


def test_plant_anchors_doc_without_images_or_cites_is_untouched():
    doc = pf.Doc(pf.Para(pf.Str("nothing to anchor here")))
    doc, counts = plant_anchors(doc)
    assert counts.figures == 0
    assert counts.citations == 0
    assert doc.content[0].content[0].text == "nothing to anchor here"


def test_anchors_survive_latex_emission_unescaped():
    """End-to-end through pandoc's real json->latex writer (not just the
    AST): the "%%" anchor markers must appear literally in the emitted
    LaTeX text, in document order, not escaped to "\\%\\%".
    """
    doc = pf.Doc(
        pf.Para(pf.Str("intro")),
        pf.Para(pf.Image(pf.Str("alt1"), url="img1.png")),
        pf.Para(pf.Cite(pf.Str("[1]"), citations=[pf.Citation(id="smith2020")])),
        pf.Para(pf.Image(pf.Str("alt2"), url="img2.png")),
    )
    doc, counts = plant_anchors(doc)

    buf = io.StringIO()
    pf.dump(doc, buf)
    tex = pypandoc.convert_text(buf.getvalue(), to="latex", format="json")

    assert "%%FIGURE:1%%" in tex
    assert "%%CITE:1%%" in tex
    assert "%%FIGURE:2%%" in tex
    assert "\\%\\%" not in tex  # would indicate escaping corrupted the anchor

    # document order
    assert tex.index("%%FIGURE:1%%") < tex.index("%%CITE:1%%") < tex.index("%%FIGURE:2%%")
