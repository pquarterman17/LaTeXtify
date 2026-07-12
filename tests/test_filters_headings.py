"""Promotion of TYPED (unstyled) section headings to real Header nodes (gap 7).

Real manuscripts author section headings as bare ALL-CAPS / numbered lines or
Word ListParagraph text with no heading style, so pandoc yields either a bold
paragraph or a single-item enumerate list and the document gets zero
``\\section`` commands. ``promote_pseudo_headings`` rewrites those into
``Header`` nodes. Tests cover every recognized shape AND the critical negative:
a genuine content list must survive untouched.
"""

from __future__ import annotations

import panflute as pf

from latextify.ingest.filters import promote_pseudo_headings


def _promote(*blocks: pf.Element) -> pf.Doc:
    doc = pf.Doc(*blocks)
    doc, _findings = promote_pseudo_headings(doc)
    return doc


def _header(doc: pf.Doc, index: int = 0) -> pf.Header:
    block = doc.content[index]
    assert isinstance(block, pf.Header), f"block {index} is {type(block).__name__}, not Header"
    return block


# --------------------------------------------------------------------------- #
# positive cases: each typed-heading shape becomes a Header
# --------------------------------------------------------------------------- #


def test_all_caps_list_item_becomes_section():
    # The YIG shape: a ListParagraph heading pandoc read as a single-item list.
    doc = _promote(pf.OrderedList(pf.ListItem(pf.Para(pf.Str("INTRODUCTION")))))
    header = _header(doc)
    assert header.level == 1
    assert pf.stringify(header) == "INTRODUCTION"


def test_all_caps_bold_paragraph_becomes_section():
    # "REFERENCES"/"ACKNOWLEDGEMENTS" typed as a bare bold paragraph.
    doc = _promote(pf.Para(pf.Strong(pf.Str("REFERENCES"))))
    header = _header(doc)
    assert header.level == 1
    assert pf.stringify(header) == "REFERENCES"


def test_roman_numbered_inline_heading_strips_the_numeral():
    doc = _promote(pf.Para(pf.Str("I."), pf.Space(), pf.Str("Introduction")))
    header = _header(doc)
    assert header.level == 1
    assert pf.stringify(header) == "Introduction"  # roman prefix dropped (revtex renumbers)


def test_arabic_numbered_subheading_gets_its_depth():
    doc = _promote(pf.Para(pf.Str("1.1"), pf.Space(), pf.Str("Methods")))
    header = _header(doc)
    assert header.level == 2  # one dot -> subsection
    assert pf.stringify(header) == "Methods"


def test_multiple_headings_across_one_document():
    doc = _promote(
        pf.OrderedList(pf.ListItem(pf.Para(pf.Str("METHODS")))),
        pf.Para(pf.Str("Some body prose that is clearly not a heading at all.")),
        pf.Para(pf.Strong(pf.Str("RESULTS"))),
    )
    assert isinstance(doc.content[0], pf.Header)
    assert isinstance(doc.content[1], pf.Para)  # prose untouched
    assert isinstance(doc.content[2], pf.Header)


# --------------------------------------------------------------------------- #
# negative cases: genuine content must NOT be promoted
# --------------------------------------------------------------------------- #


def test_genuine_bullet_list_of_sentences_stays_a_list():
    doc = _promote(
        pf.BulletList(
            pf.ListItem(pf.Para(pf.Str("First"), pf.Space(), pf.Str("point."))),
            pf.ListItem(pf.Para(pf.Str("Second"), pf.Space(), pf.Str("point."))),
        )
    )
    assert isinstance(doc.content[0], pf.BulletList)


def test_mixed_case_list_item_stays_a_list():
    # Title-case single-word items (e.g. "Apples") are not ALL-CAPS -> not headings.
    doc = _promote(
        pf.OrderedList(
            pf.ListItem(pf.Para(pf.Str("Apples"))),
            pf.ListItem(pf.Para(pf.Str("Oranges"))),
        )
    )
    assert isinstance(doc.content[0], pf.OrderedList)


def test_ordinary_paragraph_is_left_alone():
    prose = "This is an ordinary sentence of body text, mixed case, ending with a period."
    doc = _promote(pf.Para(pf.Str(prose)))
    assert isinstance(doc.content[0], pf.Para)


def test_partly_heading_list_is_not_promoted():
    # If even one item is genuine content, the whole list is left intact -- a
    # heading list is all-or-nothing.
    doc = _promote(
        pf.OrderedList(
            pf.ListItem(pf.Para(pf.Str("INTRODUCTION"))),
            pf.ListItem(pf.Para(pf.Str("a real numbered point that continues as a sentence."))),
        )
    )
    assert isinstance(doc.content[0], pf.OrderedList)


def test_long_all_caps_line_is_not_a_heading():
    # A shout-y but genuinely long line is prose, not a section heading.
    shout = "THIS IS A VERY LONG ALL CAPS SENTENCE THAT EXCEEDS THE HEADING LENGTH LIMIT BY FAR"
    doc = _promote(pf.Para(pf.Str(shout)))
    assert isinstance(doc.content[0], pf.Para)
