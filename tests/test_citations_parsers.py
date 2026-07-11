"""Zotero / Mendeley CSL-JSON parsing into RefEntry."""

from __future__ import annotations

from latextify.citations import mendeley, zotero

ARTICLE_ITEM = {
    "id": 7,
    "type": "article-journal",
    "title": "Spin dynamics in NiO",
    "container-title": "Physical Review Letters",
    "DOI": "10.1103/PhysRevLett.1.1",
    "volume": "1",
    "issue": "2",
    "page": "10-20",
    "author": [{"family": "Doe", "given": "Jane"}, {"family": "Roe", "given": "Sam"}],
    "issued": {"date-parts": [[1998, 3]]},
}


def test_csl_item_to_refentry_maps_all_fields():
    entry = zotero.csl_item_to_refentry(ARTICLE_ITEM, "zotero")
    assert entry.entry_type == "article"
    assert entry.csl_type == "article-journal"
    assert entry.title == "Spin dynamics in NiO"
    assert entry.container_title == "Physical Review Letters"
    assert entry.doi == "10.1103/PhysRevLett.1.1"
    assert entry.volume == "1"
    assert entry.issue == "2"
    assert entry.pages == "10-20"
    assert entry.year == "1998"
    assert entry.source == "zotero"
    assert entry.raw_id == "7"
    assert [a.family for a in entry.authors] == ["Doe", "Roe"]


def test_year_accepts_int_and_string_date_parts():
    int_year = zotero.csl_item_to_refentry(
        {"type": "book", "issued": {"date-parts": [[2005]]}}, "zotero"
    )
    str_year = zotero.csl_item_to_refentry(
        {"type": "book", "issued": {"date-parts": [["2021", "01"]]}}, "mendeley"
    )
    assert int_year.year == "2005"
    assert str_year.year == "2021"


def test_year_falls_back_to_raw_or_literal():
    raw = zotero.csl_item_to_refentry(
        {"type": "book", "issued": {"raw": "circa 1876"}}, "zotero"
    )
    assert raw.year == "1876"


def test_missing_year_is_none():
    entry = zotero.csl_item_to_refentry({"type": "book", "title": "X"}, "zotero")
    assert entry.year is None


def test_institutional_literal_author():
    entry = zotero.csl_item_to_refentry(
        {"type": "report", "author": [{"literal": "CERN Collaboration"}]}, "zotero"
    )
    assert len(entry.authors) == 1
    assert entry.authors[0].is_literal
    assert entry.authors[0].literal == "CERN Collaboration"


def test_csl_type_mapping():
    def etype(csl: str) -> str:
        return zotero.csl_item_to_refentry({"type": csl}, "zotero").entry_type

    assert etype("article-journal") == "article"
    assert etype("paper-conference") == "inproceedings"
    assert etype("book") == "book"
    assert etype("chapter") == "incollection"
    assert etype("something-unknown") == "misc"


def test_extract_json_handles_trailing_text_and_junk():
    payload = zotero.extract_json('ADDIN ZOTERO_ITEM CSL_CITATION {"a": 1} trailing')
    assert payload == {"a": 1}
    assert zotero.extract_json("ADDIN ZOTERO_ITEM CSL_CITATION no-json-here") == {}
    assert zotero.extract_json("ADDIN X {broken json") == {}


def test_parse_instruction_multi_item():
    instr = (
        'ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems": ['
        '{"itemData": {"type": "book", "title": "A", "id": 1}},'
        '{"itemData": {"type": "article-journal", "title": "B", "id": 2}}]}'
    )
    entries = zotero.parse_instruction(instr)
    assert [e.title for e in entries] == ["A", "B"]
    assert entries[0].source == "zotero"


def test_mendeley_matches_but_not_zotero():
    assert mendeley.matches("ADDIN CSL_CITATION {}")
    assert not mendeley.matches("ADDIN ZOTERO_ITEM CSL_CITATION {}")
    assert zotero.matches("ADDIN ZOTERO_ITEM CSL_CITATION {}")
    assert not zotero.matches("ADDIN CSL_CITATION {}")


def test_mendeley_parse_sets_source():
    instr = (
        'ADDIN CSL_CITATION {"citationItems": '
        '[{"itemData": {"type": "article-journal", "id": 1}}]}'
    )
    entries = mendeley.parse_instruction(instr)
    assert len(entries) == 1
    assert entries[0].source == "mendeley"
