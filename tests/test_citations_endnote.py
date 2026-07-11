"""EndNote field instruction -> RefEntry parsing, including malformed data."""

from __future__ import annotations

from latextify.citations import endnote

SINGLE = (
    "ADDIN EN.CITE <EndNote><Cite><Author>Feynman</Author><Year>1969</Year>"
    "<RecNum>1</RecNum><DisplayText>[1]</DisplayText><record>"
    "<rec-number>1</rec-number>"
    '<ref-type name="Journal Article">17</ref-type>'
    "<contributors><authors>"
    "<author>Feynman, Richard P.</author>"
    "<author>Gell-Mann, Murray</author>"
    "</authors></contributors>"
    "<titles><title>Quantum electrodynamics of high-energy collisions</title></titles>"
    "<periodical><full-title>Physical Review</full-title></periodical>"
    "<pages>1-10</pages><volume>1</volume>"
    "<dates><year>1969</year></dates>"
    "<electronic-resource-num>10.1103/PhysRev.1969.1</electronic-resource-num>"
    "</record></Cite></EndNote>"
)

MULTI = (
    "ADDIN EN.CITE <EndNote>"
    "<Cite><Author>Pathria</Author><Year>1972</Year><RecNum>2</RecNum>"
    "<record><rec-number>2</rec-number>"
    '<ref-type name="Book">6</ref-type>'
    "<contributors><authors><author>Pathria, R. K.</author></authors></contributors>"
    "<titles><title>Statistical Mechanics</title></titles>"
    "<dates><year>1972</year></dates>"
    "</record></Cite>"
    "<Cite><Author>Turing</Author><Year>1950</Year><RecNum>3</RecNum>"
    "<record><rec-number>3</rec-number>"
    '<ref-type name="Conference Paper">47</ref-type>'
    "<contributors><authors><author>Turing, Alan</author></authors></contributors>"
    "<titles><title>Scalable EndNote parsing</title>"
    "<secondary-title>Proceedings of Computing History</secondary-title></titles>"
    "<dates><year>1950</year></dates>"
    "</record></Cite>"
    "</EndNote>"
)

# <style> wrapping around leaf text -- common in real EndNote-exported XML.
STYLE_WRAPPED = (
    "ADDIN EN.CITE <EndNote><Cite><RecNum>9</RecNum><record>"
    "<rec-number>9</rec-number>"
    '<ref-type name="Journal Article">17</ref-type>'
    "<contributors><authors><author>"
    '<style face="normal" font="default" size="100%">Curie, Marie</style>'
    "</author></authors></contributors>"
    "<titles><title>"
    '<style face="normal" font="default" size="100%">Radioactive substances</style>'
    "</title></titles>"
    "<dates><year>"
    '<style face="normal" font="default" size="100%">1903</style>'
    "</year></dates>"
    "</record></Cite></EndNote>"
)


def test_matches_requires_en_cite_marker():
    assert endnote.matches("ADDIN EN.CITE <EndNote></EndNote>")
    assert not endnote.matches("ADDIN ZOTERO_ITEM CSL_CITATION {}")
    assert not endnote.matches("ADDIN CSL_CITATION {}")


def test_single_citation_all_fields_mapped():
    entries = endnote.parse_instruction(SINGLE)
    assert len(entries) == 1
    e = entries[0]
    assert e.entry_type == "article"
    assert e.title == "Quantum electrodynamics of high-energy collisions"
    assert [a.family for a in e.authors] == ["Feynman", "Gell-Mann"]
    assert e.authors[0].given == "Richard P."
    assert e.year == "1969"
    assert e.container_title == "Physical Review"
    assert e.volume == "1"
    assert e.pages == "1-10"
    assert e.doi == "10.1103/PhysRev.1969.1"
    assert e.raw_id == "1"
    assert e.source == "endnote"


def test_multi_cite_field_yields_two_entries_in_order():
    entries = endnote.parse_instruction(MULTI)
    assert [e.title for e in entries] == [
        "Statistical Mechanics",
        "Scalable EndNote parsing",
    ]
    assert entries[0].entry_type == "book"
    assert entries[1].entry_type == "inproceedings"


def test_secondary_title_fallback_used_as_journal_when_no_periodical():
    entries = endnote.parse_instruction(MULTI)
    conf_paper = entries[1]
    assert conf_paper.container_title == "Proceedings of Computing History"


def test_style_wrapped_leaf_text_unwrapped():
    entries = endnote.parse_instruction(STYLE_WRAPPED)
    assert len(entries) == 1
    e = entries[0]
    assert e.authors[0].family == "Curie"
    assert e.title == "Radioactive substances"
    assert e.year == "1903"


def test_double_html_encoded_fragment_is_parsed():
    # Simulates EndNote's occasional extra HTML-entity encoding layer: after
    # document.xml's own entities are unescaped once by lxml, the instruction
    # string still contains literal "&lt;EndNote&gt;..." rather than raw XML.
    encoded = SINGLE.replace("ADDIN EN.CITE ", "ADDIN EN.CITE ")
    xml_part = encoded[encoded.index("<EndNote>") :]
    double_encoded = (
        xml_part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    instruction = "ADDIN EN.CITE " + double_encoded
    entries = endnote.parse_instruction(instruction)
    assert len(entries) == 1
    assert entries[0].title == "Quantum electrodynamics of high-energy collisions"


# --- graceful degradation: malformed/unknown field data never crashes -------


def test_unclosed_xml_degrades_to_empty_list():
    broken = "ADDIN EN.CITE <EndNote><Cite><record><titles><title>Broken"
    assert endnote.parse_instruction(broken) == []


def test_non_xml_junk_degrades_to_empty_list():
    assert endnote.parse_instruction("ADDIN EN.CITE not xml at all") == []


def test_cite_without_record_is_skipped_not_crashed():
    no_record = "ADDIN EN.CITE <EndNote><Cite><Author>X</Author></Cite></EndNote>"
    assert endnote.parse_instruction(no_record) == []


def test_empty_endnote_element_yields_no_entries():
    assert endnote.parse_instruction("ADDIN EN.CITE <EndNote></EndNote>") == []
