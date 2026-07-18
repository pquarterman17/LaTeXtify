"""Parse a user-supplied EndNote XML library export into RefEntry records."""

from __future__ import annotations

import pytest

from latextify.citations.endnote_xml_in import parse_endnote_xml

TWO_RECORDS = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<xml><records>"
    b"<record>"
    b"<rec-number>1</rec-number>"
    b'<ref-type name="Journal Article">17</ref-type>'
    b"<contributors><authors>"
    b"<author>Placeholder, Pat</author>"
    b"<author>Example, Evan</author>"
    b"</authors></contributors>"
    b"<titles><title>Strain-tunable magnon transport in a placeholder oxide</title></titles>"
    b"<periodical><full-title>Journal of Placeholder Physics</full-title></periodical>"
    b"<pages>100-110</pages><volume>12</volume>"
    b"<dates><year>2020</year></dates>"
    b"<electronic-resource-num>10.1000/placeholder.2020</electronic-resource-num>"
    b"</record>"
    b"<record>"
    b"<rec-number>2</rec-number>"
    b'<ref-type name="Book">6</ref-type>'
    b"<contributors><authors><author>Sample, Sam</author></authors></contributors>"
    b"<titles><title>Fundamentals of Placeholder Materials</title></titles>"
    b"<dates><year>1999</year></dates>"
    b"</record>"
    b"</records></xml>"
)

# <style> wrapping around leaf text -- the shape a real EndNote export uses.
STYLE_WRAPPED = (
    b"<xml><records><record>"
    b"<rec-number>9</rec-number>"
    b'<ref-type name="Journal Article">17</ref-type>'
    b"<contributors><authors><author>"
    b'<style face="normal" font="default" size="100%">Curie-Placeholder, Marie</style>'
    b"</author></authors></contributors>"
    b"<titles><title>"
    b'<style face="normal" font="default" size="100%">A Placeholder Title</style>'
    b"</title></titles>"
    b"<dates><year>"
    b'<style face="normal" font="default" size="100%">1903</style>'
    b"</year></dates>"
    b"</record></records></xml>"
)


def test_parses_two_records_all_fields_mapped():
    entries = parse_endnote_xml(TWO_RECORDS)
    assert len(entries) == 2

    article = entries[0]
    assert article.key == "1"
    assert article.raw_id == "1"
    assert article.entry_type == "article"
    assert article.title == "Strain-tunable magnon transport in a placeholder oxide"
    assert [(a.family, a.given) for a in article.authors] == [
        ("Placeholder", "Pat"),
        ("Example", "Evan"),
    ]
    assert article.year == "2020"
    assert article.container_title == "Journal of Placeholder Physics"
    assert article.volume == "12"
    assert article.pages == "100-110"
    assert article.doi == "10.1000/placeholder.2020"
    assert article.source == "endnote-xml"

    book = entries[1]
    assert book.key == "2"
    assert book.entry_type == "book"
    assert book.title == "Fundamentals of Placeholder Materials"


def test_style_wrapped_leaf_text_unwrapped():
    (entry,) = parse_endnote_xml(STYLE_WRAPPED)
    assert entry.authors[0].family == "Curie-Placeholder"
    assert entry.title == "A Placeholder Title"
    assert entry.year == "1903"


def test_missing_fields_tolerated():
    minimal = (
        b"<xml><records><record>"
        b"<rec-number>5</rec-number>"
        b"<titles><title>Bare Record</title></titles>"
        b"</record></records></xml>"
    )
    (entry,) = parse_endnote_xml(minimal)
    assert entry.title == "Bare Record"
    assert entry.authors == ()
    assert entry.year is None
    assert entry.container_title is None
    assert entry.doi is None


def test_record_without_rec_number_stays_keyless():
    no_rec_number = (
        b"<xml><records><record>"
        b"<titles><title>No id</title></titles>"
        b"</record></records></xml>"
    )
    (entry,) = parse_endnote_xml(no_rec_number)
    assert entry.key == ""
    assert entry.raw_id is None


def test_malformed_xml_raises_value_error():
    with pytest.raises(ValueError, match="not valid XML"):
        parse_endnote_xml(b"<xml><records><record>unterminated")


def test_non_endnote_xml_raises_value_error():
    with pytest.raises(ValueError, match="not a valid EndNote XML export"):
        parse_endnote_xml(b"<not-endnote><foo>bar</foo></not-endnote>")


def test_external_entity_is_never_resolved():
    # Classic XXE payload: an external entity that would read a local file if
    # resolved. resolve_entities=False (+ load_dtd=False) leaves it as an
    # unexpanded entity node instead of raising OR substituting its target's
    # contents -- parsing succeeds, but the "title" never carries /etc/passwd.
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE xml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<xml><records><record>"
        b"<rec-number>1</rec-number>"
        b"<titles><title>&xxe;</title></titles>"
        b"</record></records></xml>"
    )
    (entry,) = parse_endnote_xml(xxe)
    assert entry.title is None
