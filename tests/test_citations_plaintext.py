"""Plain-text marker detection, reference-list segmentation, and body linkage.

Pure-Python tests that need no Crossref network access: the linkage tests build a
:class:`PlaintextResult` directly, and the segmentation tests build tiny .docx
files with python-docx.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from latextify.citations.plaintext import (
    PlaintextResult,
    expand_numeric_range,
    link_body_markers,
    segment_reference_list,
    strip_reference_section,
)

# --------------------------------------------------------------------------- #
# numeric range expansion
# --------------------------------------------------------------------------- #


def test_expand_single():
    assert expand_numeric_range("12") == [12]


def test_expand_range_and_list():
    assert expand_numeric_range("3-5,8") == [3, 4, 5, 8]


def test_expand_multiple_ranges():
    assert expand_numeric_range("1-3, 7, 9-10") == [1, 2, 3, 7, 9, 10]


def test_expand_unicode_dash():
    assert expand_numeric_range("3–5") == [3, 4, 5]  # en dash


def test_expand_non_numeric_returns_empty():
    assert expand_numeric_range("see note") == []


def test_expand_reversed_range_kept_as_endpoints():
    assert expand_numeric_range("5-3") == [5, 3]


# --------------------------------------------------------------------------- #
# reference-list segmentation
# --------------------------------------------------------------------------- #


def _make_docx(path: Path, heading: str, refs: list[str], *, numbered=True) -> Path:
    doc = Document()
    doc.add_heading("A Title", level=0)
    doc.add_heading("Body", level=1)
    doc.add_paragraph("Some body text citing [1] and [2].")
    doc.add_heading(heading, level=1)
    for i, ref in enumerate(refs, start=1):
        doc.add_paragraph(f"{i}. {ref}" if numbered else ref)
    doc.save(path)
    return path


def test_segment_finds_numbered_references(tmp_path):
    docx = _make_docx(
        tmp_path / "r.docx",
        "References",
        ["Smith, A. First. J. A 1 (2020).", "Jones, B. Second. J. B 2 (2019)."],
    )
    reflist = segment_reference_list(docx)
    assert reflist.found
    assert reflist.heading == "References"
    assert [r.number for r in reflist.references] == [1, 2]
    assert reflist.references[0].text.startswith("Smith")
    assert "1." not in reflist.references[0].text  # leading number stripped


def test_segment_bibliography_heading(tmp_path):
    docx = _make_docx(tmp_path / "b.docx", "Bibliography", ["Doe, J. Only. J. 1 (2021)."])
    reflist = segment_reference_list(docx)
    assert reflist.found
    assert reflist.heading == "Bibliography"


def test_segment_unnumbered_references(tmp_path):
    docx = _make_docx(
        tmp_path / "u.docx",
        "References",
        ["Smith, A. First paper. (2020).", "Jones, B. Second paper. (2019)."],
        numbered=False,
    )
    reflist = segment_reference_list(docx)
    assert reflist.found
    assert [r.number for r in reflist.references] == [None, None]


def test_segment_bracketed_numbers(tmp_path):
    doc = Document()
    doc.add_heading("References", level=1)
    doc.add_paragraph("[1] Smith, A. First. (2020).")
    doc.add_paragraph("[2] Jones, B. Second. (2019).")
    path = tmp_path / "brk.docx"
    doc.save(path)
    reflist = segment_reference_list(path)
    assert [r.number for r in reflist.references] == [1, 2]
    assert reflist.references[0].text.startswith("Smith")


def test_segment_no_reference_list(tmp_path):
    doc = Document()
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph("Body text with no reference section at all.")
    path = tmp_path / "none.docx"
    doc.save(path)
    reflist = segment_reference_list(path)
    assert not reflist.found
    assert reflist.heading is None
    assert reflist.references == []


def test_segment_skips_empty_paragraphs(tmp_path):
    doc = Document()
    doc.add_heading("References", level=1)
    doc.add_paragraph("1. Smith, A. First. (2020).")
    doc.add_paragraph("")  # blank line between references
    doc.add_paragraph("2. Jones, B. Second. (2019).")
    path = tmp_path / "gap.docx"
    doc.save(path)
    reflist = segment_reference_list(path)
    assert [r.number for r in reflist.references] == [1, 2]


# --------------------------------------------------------------------------- #
# body linkage
# --------------------------------------------------------------------------- #


def _result(**over) -> PlaintextResult:
    defaults = dict(
        keys_by_number={
            1: "smith2020",
            2: "jones2019",
            3: "brown2018",
            4: "chen2017",
            8: "lee2015",
        },
        author_year_keys={("smith", "2020"): ["smith2020"]},
        has_reference_list=True,
        heading="References",
    )
    defaults.update(over)
    return PlaintextResult(**defaults)


def test_link_single_numeric_marker():
    tex, warnings = link_body_markers("Shown {[}1{]} here.", _result())
    assert tex == "Shown \\cite{smith2020} here."
    assert warnings == []


def test_link_numeric_range_marker():
    tex, warnings = link_body_markers("Groups {[}3-5,8{]} explored.", _result())
    # 5 is absent from keys_by_number -> partial, but 3,4,8 still link.
    assert "\\cite{brown2018,chen2017,lee2015}" in tex
    assert any("no reference numbered 5" in w for w in warnings)


def test_link_superscript_marker():
    tex, warnings = link_body_markers("Reviews\\textsuperscript{1,2} summarize.", _result())
    assert tex == "Reviews\\cite{smith2020,jones2019} summarize."
    assert warnings == []


def test_link_author_year_marker():
    tex, warnings = link_body_markers("The protocol (Smith et al., 2020) is key.", _result())
    assert tex == "The protocol \\cite{smith2020} is key."
    assert warnings == []


def test_link_author_year_across_line_wrap():
    # pandoc wraps long lines; the marker may straddle a newline.
    tex, _ = link_body_markers("... protocol (Smith et al.,\n2020) is key.", _result())
    assert "\\cite{smith2020}" in tex


def test_out_of_range_numeric_marker_warns_and_is_left():
    tex, warnings = link_body_markers("Bad {[}99{]} marker.", _result())
    assert "{[}99{]}" in tex  # untouched
    assert "\\cite" not in tex
    assert any("99" in w for w in warnings)


def test_non_numeric_bracket_is_not_touched_or_warned():
    tex, warnings = link_body_markers("An aside {[}see appendix{]} here.", _result())
    assert tex == "An aside {[}see appendix{]} here."
    assert warnings == []


def test_superscript_ordinal_not_treated_as_citation():
    # "1\textsuperscript{st}" style ordinal has non-numeric content -> ignored.
    tex, warnings = link_body_markers("the 1\\textsuperscript{st} case", _result())
    assert tex == "the 1\\textsuperscript{st} case"
    assert warnings == []


def test_unknown_author_year_with_known_year_warns():
    result = _result(author_year_keys={("smith", "2020"): ["smith2020"]})
    tex, warnings = link_body_markers("As per (Nobody et al., 2020) here.", result)
    assert "(Nobody et al., 2020)" in tex  # untouched
    assert any("Nobody" in w for w in warnings)


def test_unknown_author_year_with_unknown_year_is_silent():
    # A parenthetical with a year the bibliography never mentions is likely prose.
    result = _result(author_year_keys={("smith", "2020"): ["smith2020"]})
    tex, warnings = link_body_markers("Founded (Acme Corp, 1889) long ago.", result)
    assert "(Acme Corp, 1889)" in tex
    assert warnings == []


def test_no_reference_list_leaves_markers_untouched():
    result = PlaintextResult(has_reference_list=False)
    tex, warnings = link_body_markers("Marker {[}1{]}.", result)
    # keys_by_number empty -> unresolved, warned, left in place.
    assert "{[}1{]}" in tex
    assert warnings


# --------------------------------------------------------------------------- #
# reference-section stripping
# --------------------------------------------------------------------------- #


def test_strip_removes_reference_section_to_eof():
    tex = (
        "\\section{Introduction}\\label{introduction}\n\n"
        "Body text.\n\n"
        "\\section{References}\\label{references}\n\n"
        "1. Smith, A. First. (2020).\n\n2. Jones, B. Second. (2019).\n"
    )
    stripped = strip_reference_section(tex, _result())
    assert "\\section{References}" not in stripped
    assert "Smith, A. First" not in stripped
    assert "\\section{Introduction}" in stripped
    assert "Body text." in stripped


def test_strip_no_op_when_no_reference_heading():
    tex = "\\section{Introduction}\\label{introduction}\n\nBody only.\n"
    assert strip_reference_section(tex, _result()) == tex


def test_strip_no_op_when_result_has_no_reference_list():
    tex = "\\section{References}\\label{references}\n\n1. Smith. (2020).\n"
    result = PlaintextResult(has_reference_list=False)
    assert strip_reference_section(tex, result) == tex
