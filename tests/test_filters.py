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
    normalize_tables,
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


# ---------------------------
# normalize_tables
# ---------------------------


def _simple_table(*, header: bool = True) -> pf.Table:
    """A clean 2-column table: text ID column, numeric-majority value column."""
    body_rows = [
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("A"))), pf.TableCell(pf.Plain(pf.Str("3.14")))),
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("B"))), pf.TableCell(pf.Plain(pf.Str("-2.5")))),
    ]
    head = None
    if header:
        head = pf.TableHead(
            pf.TableRow(
                pf.TableCell(pf.Plain(pf.Str("Sample"))),
                pf.TableCell(pf.Plain(pf.Str("Value"))),
            )
        )
    return pf.Table(pf.TableBody(*body_rows), head=head)


def test_clean_table_becomes_a_single_rawblock():
    doc = pf.Doc(_simple_table(), api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert findings == []
    assert len(doc.content) == 1
    assert isinstance(doc.content[0], pf.RawBlock)
    assert doc.content[0].format == "latex"


def test_clean_table_uses_booktabs_rules_and_no_vertical_bars():
    doc = pf.Doc(_simple_table(), api_version=(1, 23, 1))
    doc, _ = normalize_tables(doc)
    tex = doc.content[0].text

    assert "\\toprule" in tex
    assert "\\midrule" in tex
    assert "\\bottomrule" in tex
    assert "|" not in tex  # no vertical rules anywhere, incl. the column spec
    assert "\\begin{tabular}{lr}" in tex  # text col left, numeric-majority col right
    assert "Sample & Value \\\\" in tex
    assert "A & 3.14 \\\\" in tex
    assert "B & -2.5 \\\\" in tex


def test_clean_table_without_header_row_skips_midrule():
    doc = pf.Doc(_simple_table(header=False), api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert findings == []
    tex = doc.content[0].text
    assert "\\midrule" not in tex
    assert "\\toprule" in tex
    assert "\\bottomrule" in tex


def test_horizontal_merge_becomes_multicolumn():
    header = pf.TableHead(
        pf.TableRow(
            pf.TableCell(pf.Plain(pf.Str("Run"))),
            pf.TableCell(pf.Plain(pf.Str("Measurement")), colspan=2),
        )
    )
    body = pf.TableBody(
        pf.TableRow(
            pf.TableCell(pf.Plain(pf.Str("1"))),
            pf.TableCell(pf.Plain(pf.Str("10"))),
            pf.TableCell(pf.Plain(pf.Str("K"))),
        )
    )
    table = pf.Table(body, head=header)
    doc = pf.Doc(table, api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert findings == []
    tex = doc.content[0].text
    assert "\\multicolumn{2}{c}{Measurement}" in tex


def test_column_alignment_respects_explicit_pandoc_alignment():
    # All-numeric column, but pandoc's own colspec says AlignLeft -- that
    # must win over the numeric-majority inference (which would say right).
    body = pf.TableBody(
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("1")))),
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("2")))),
    )
    table = pf.Table(body, colspec=[("AlignLeft", "ColWidthDefault")])
    doc = pf.Doc(table, api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert findings == []
    tex = doc.content[0].text
    assert "\\begin{tabular}{l}" in tex


def test_vmerge_table_degrades_to_booktabs_with_duplicated_content_and_a_finding():
    # A rowspan>1 cell (Word's vMerge) makes this table pathological (plan
    # item 25): it is no longer left for pandoc's own default table writer
    # (that output doesn't compile in fragment mode -- see the module
    # docstring) -- instead it's degraded to a booktabs table that duplicates
    # the merged cell's content into every row it spanned.
    body = pf.TableBody(
        pf.TableRow(
            pf.TableCell(pf.Plain(pf.Str("1"))),
            pf.TableCell(pf.Plain(pf.Str("10")), rowspan=2),
        ),
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("2")))),
    )
    table = pf.Table(body)
    doc = pf.Doc(table, api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert len(findings) == 1
    assert "table 1" in findings[0].message
    assert "vertically merged cell (vMerge)" in findings[0].message
    assert "verify the structure against the source document" in findings[0].message
    # Reconstructed: a RawBlock, not left as a raw Table node.
    assert isinstance(doc.content[0], pf.RawBlock)
    tex = doc.content[0].text
    assert "\\multirow" not in tex  # ours duplicates instead of using multirow
    assert "\\longtable" not in tex
    assert "1 & 10 \\\\" in tex
    assert "2 & 10 \\\\" in tex  # the merged cell's content duplicated, not dropped
    assert "[table structure simplified -- verify against source]" in tex


def test_nested_table_flattened_into_the_enclosing_tables_cell():
    # A nested table (plan item 25) can't become a second tabular inside a
    # cell (not legal LaTeX), so its content is flattened to plain text
    # instead -- content survives, the nested structure does not.
    inner = pf.Table(
        pf.TableBody(
            pf.TableRow(pf.TableCell(pf.Plain(pf.Str("inner1")))),
            pf.TableRow(pf.TableCell(pf.Plain(pf.Str("inner2")))),
        )
    )
    outer = pf.Table(pf.TableBody(pf.TableRow(pf.TableCell(inner))))
    doc = pf.Doc(outer, api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    assert len(findings) == 1
    assert "table 1" in findings[0].message
    assert "nested table" in findings[0].message
    # Reconstructed once, as a single RawBlock -- the inner table is never
    # independently turned into its own RawBlock (which would be an illegal
    # float inside the outer table's cell).
    assert isinstance(doc.content[0], pf.RawBlock)
    tex = doc.content[0].text
    assert "inner1" in tex
    assert "inner2" in tex
    assert "\\begin{tabular}" in tex
    assert tex.count("\\begin{tabular}") == 1  # only the outer table's own


def test_sibling_clean_table_unaffected_by_a_pathological_table():
    vmerge_body = pf.TableBody(
        pf.TableRow(
            pf.TableCell(pf.Plain(pf.Str("1"))),
            pf.TableCell(pf.Plain(pf.Str("x")), rowspan=2),
        ),
        pf.TableRow(pf.TableCell(pf.Plain(pf.Str("2")))),
    )
    pathological = pf.Table(vmerge_body)
    clean = _simple_table()
    doc = pf.Doc(pathological, clean, api_version=(1, 23, 1))
    doc, findings = normalize_tables(doc)

    # Reference: the clean table's own conversion in isolation, to prove
    # processing a pathological sibling first doesn't perturb it byte-for-byte.
    reference_doc = pf.Doc(_simple_table(), api_version=(1, 23, 1))
    reference_doc, _ = normalize_tables(reference_doc)

    assert len(findings) == 1
    assert "table 1" in findings[0].message  # the pathological one, first in doc order
    assert isinstance(doc.content[0], pf.RawBlock)  # degraded, not left alone
    assert isinstance(doc.content[1], pf.RawBlock)  # the clean sibling still converts
    assert doc.content[1].text == reference_doc.content[0].text


def test_doc_without_tables_is_untouched_and_finding_free():
    doc = pf.Doc(pf.Para(pf.Str("no tables here")))
    doc, findings = normalize_tables(doc)
    assert findings == []
    assert isinstance(doc.content[0], pf.Para)
