"""Complex-field walker: run concatenation, nesting, ordering, classification."""

from __future__ import annotations

from latextify.citations.fields import (
    assemble_fields,
    classify_marker,
    flatten_fields,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _doc(body: str) -> bytes:
    return (
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    ).encode()


def _begin() -> str:
    return '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'


def _sep() -> str:
    return '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'


def _end() -> str:
    return '<w:r><w:fldChar w:fldCharType="end"/></w:r>'


def _instr(text: str) -> str:
    return f'<w:r><w:instrText xml:space="preserve">{text}</w:instrText></w:r>'


def test_instr_text_concatenated_across_runs():
    body = "<w:p>" + _begin() + _instr("ADDIN ZOTE") + _instr("RO_ITEM ") + _instr(
        "CSL_CITATION {}"
    ) + _sep() + "<w:r><w:t>[1]</w:t></w:r>" + _end() + "</w:p>"
    fields = flatten_fields(assemble_fields(_doc(body)))
    assert len(fields) == 1
    assert fields[0].instruction == "ADDIN ZOTERO_ITEM CSL_CITATION {}"


def test_instr_text_after_separate_is_ignored():
    # Text runs (and any instrText) after 'separate' are the field RESULT and
    # must not leak into the instruction.
    body = (
        "<w:p>"
        + _begin()
        + _instr("TIME \\@ yyyy")
        + _sep()
        + _instr("2026")  # result-side; should be dropped
        + _end()
        + "</w:p>"
    )
    fields = flatten_fields(assemble_fields(_doc(body)))
    assert fields[0].instruction == "TIME \\@ yyyy"


def test_nested_field_is_recovered_and_ordered():
    inner = _begin() + _instr("ADDIN ZOTERO_ITEM CSL_CITATION {}") + _sep() + _end()
    body = (
        "<w:p>"
        + _begin()
        + _instr("PAGEREF _Ref1 \\h")
        + inner
        + _sep()
        + "<w:r><w:t>2</w:t></w:r>"
        + _end()
        + "</w:p>"
    )
    top = assemble_fields(_doc(body))
    assert len(top) == 1  # outer PAGEREF only at top level
    assert top[0].instruction == "PAGEREF _Ref1 \\h"
    assert len(top[0].children) == 1
    flat = flatten_fields(top)
    # Pre-order: outer before inner.
    assert flat[0].instruction.startswith("PAGEREF")
    assert flat[1].instruction == "ADDIN ZOTERO_ITEM CSL_CITATION {}"


def test_document_order_preserved_for_siblings():
    def field(marker: str) -> str:
        return _begin() + _instr(marker) + _sep() + _end()

    body = "<w:p>" + field("FIRST") + field("SECOND") + field("THIRD") + "</w:p>"
    flat = flatten_fields(assemble_fields(_doc(body)))
    assert [f.instruction for f in flat] == ["FIRST", "SECOND", "THIRD"]


def test_fldsimple_is_captured():
    body = (
        '<w:p><w:fldSimple w:instr=" ADDIN CSL_CITATION {} ">'
        "<w:r><w:t>[1]</w:t></w:r></w:fldSimple></w:p>"
    )
    flat = flatten_fields(assemble_fields(_doc(body)))
    assert flat[0].instruction == "ADDIN CSL_CITATION {}"


def test_classify_marker():
    assert classify_marker("ADDIN ZOTERO_ITEM CSL_CITATION {}") == "zotero"
    assert classify_marker("ADDIN CSL_CITATION {}") == "mendeley"
    assert classify_marker("PAGEREF _Ref1 \\h") is None
    # Zotero wins even though its instruction also contains CSL_CITATION.
    assert classify_marker("ADDIN ZOTERO_ITEM CSL_CITATION {}") != "mendeley"


def test_classify_marker_endnote_and_wordnative():
    """Plan item 13: additive markers, zero changes to the other three."""
    assert classify_marker("ADDIN EN.CITE <EndNote></EndNote>") == "endnote"
    assert classify_marker("CITATION Smi20 \\l 1033") == "wordnative"
    assert classify_marker("CITATION Kit05 \\l 1033 \\m Tur50 \\l 1033") == "wordnative"


def test_unclosed_field_does_not_crash():
    body = "<w:p>" + _begin() + _instr("DANGLING") + "</w:p>"  # no end
    flat = flatten_fields(assemble_fields(_doc(body)))
    assert flat[0].instruction == "DANGLING"


def test_mixed_source_fields_keep_document_order():
    """Zotero/EndNote/wordnative markers interleave via the shared field walker.

    Plan item 13 flagged this ordering as potentially intractable for a
    wordnative sdt-based design; modeling the wordnative CITATION marker as a
    field discovered by the SAME assemble_fields walk (rather than a separate
    sdt-only walk) sidesteps the problem entirely -- document order falls out
    for free, exactly like the existing PAGEREF-nested-Zotero case.
    """

    def field(marker: str) -> str:
        return _begin() + _instr(marker) + _sep() + "<w:r><w:t>x</w:t></w:r>" + _end()

    body = (
        "<w:p>"
        + field("ADDIN ZOTERO_ITEM CSL_CITATION {}")
        + field("ADDIN EN.CITE <EndNote></EndNote>")
        + field("CITATION Smi20 \\l 1033")
        + "</w:p>"
    )
    flat = flatten_fields(assemble_fields(_doc(body)))
    kinds = [classify_marker(f.instruction) for f in flat]
    assert kinds == ["zotero", "endnote", "wordnative"]
