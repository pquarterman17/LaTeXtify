"""Word-native CITATION field parsing: tag lists, b:Source XML, resolution."""

from __future__ import annotations

import zipfile
from pathlib import Path

from latextify.citations import wordnative
from latextify.model.refs import RefEntry

B = wordnative.B


def _source_xml(*sources: str) -> bytes:
    body = "".join(sources)
    return (
        f'<b:Sources xmlns:b="{B}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'SelectedStyle="">' + body + "</b:Sources>"
    ).encode()


def _journal_source(tag: str, title: str, family: str, given: str, year: str) -> str:
    return (
        f"<b:Source><b:Tag>{tag}</b:Tag>"
        "<b:SourceType>JournalArticle</b:SourceType>"
        f"<b:Title>{title}</b:Title>"
        f"<b:JournalName>Journal of Things</b:JournalName>"
        f"<b:Year>{year}</b:Year>"
        f"<b:Volume>5</b:Volume><b:Pages>10-20</b:Pages>"
        "<b:Author><b:Author><b:NameList>"
        f"<b:Person><b:Last>{family}</b:Last><b:First>{given}</b:First></b:Person>"
        "</b:NameList></b:Author></b:Author>"
        "</b:Source>"
    )


# --- parse_tag_list -----------------------------------------------------------


def test_single_tag_no_switches():
    assert wordnative.parse_tag_list("CITATION Smi20") == ["Smi20"]


def test_single_tag_with_locale_switch():
    assert wordnative.parse_tag_list("CITATION Smi20 \\l 1033") == ["Smi20"]


def test_multi_tag_with_m_switch():
    assert wordnative.parse_tag_list(
        "CITATION Kit05 \\l 1033 \\m Tur50 \\l 1033"
    ) == ["Kit05", "Tur50"]


def test_three_tags():
    assert wordnative.parse_tag_list(
        "CITATION A1 \\l 1033 \\m A2 \\l 1033 \\m A3 \\l 1033"
    ) == ["A1", "A2", "A3"]


def test_non_citation_instruction_yields_no_tags():
    assert wordnative.parse_tag_list("PAGEREF _Ref1 \\h") == []


def test_matches_requires_citation_prefix():
    assert wordnative.matches("CITATION Smi20 \\l 1033")
    assert wordnative.matches(" CITATION Smi20 \\l 1033 ")
    assert not wordnative.matches("ADDIN ZOTERO_ITEM CSL_CITATION {}")
    assert not wordnative.matches("PAGEREF _Ref1 \\h")


# --- load_sources ---------------------------------------------------------


def test_load_sources_parses_journal_article(tmp_path: Path):
    docx = tmp_path / "test.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr(
            "customXml/item1.xml",
            _source_xml(_journal_source("Smi20", "Some Title", "Smith", "Alice", "2020")),
        )
    sources = wordnative.load_sources(docx)
    assert set(sources) == {"Smi20"}
    entry = sources["Smi20"]
    assert entry.entry_type == "article"
    assert entry.title == "Some Title"
    assert entry.authors[0].family == "Smith"
    assert entry.authors[0].given == "Alice"
    assert entry.year == "2020"
    assert entry.container_title == "Journal of Things"
    assert entry.volume == "5"
    assert entry.pages == "10-20"
    assert entry.raw_id == "Smi20"


def test_load_sources_corporate_author(tmp_path: Path):
    xml = (
        f'<b:Sources xmlns:b="{B}">'
        "<b:Source><b:Tag>Cern12</b:Tag><b:SourceType>Report</b:SourceType>"
        "<b:Title>Higgs Search</b:Title><b:Year>2012</b:Year>"
        "<b:Author><b:Author><b:Corporate>CERN Collaboration</b:Corporate>"
        "</b:Author></b:Author>"
        "</b:Source></b:Sources>"
    ).encode()
    docx = tmp_path / "t.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("customXml/item1.xml", xml)
    sources = wordnative.load_sources(docx)
    assert sources["Cern12"].authors[0].is_literal
    assert sources["Cern12"].authors[0].literal == "CERN Collaboration"
    assert sources["Cern12"].entry_type == "techreport"


def test_load_sources_ignores_unrelated_customxml_part(tmp_path: Path):
    docx = tmp_path / "test.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("customXml/item1.xml", b"<UnrelatedRoot><Foo>bar</Foo></UnrelatedRoot>")
        zf.writestr(
            "customXml/item2.xml",
            _source_xml(_journal_source("Doe19", "Other Title", "Doe", "Jane", "2019")),
        )
    sources = wordnative.load_sources(docx)
    assert set(sources) == {"Doe19"}


def test_load_sources_skips_source_missing_tag(tmp_path: Path):
    docx = tmp_path / "test.docx"
    bad_source = (
        "<b:Source><b:SourceType>Book</b:SourceType><b:Title>No Tag</b:Title></b:Source>"
    )
    good_source = _journal_source("Ok1", "Fine Title", "Ok", "P", "2021")
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("customXml/item1.xml", _source_xml(bad_source, good_source))
    sources = wordnative.load_sources(docx)
    assert set(sources) == {"Ok1"}


def test_load_sources_malformed_xml_part_skipped_not_crashed(tmp_path: Path):
    docx = tmp_path / "test.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("customXml/item1.xml", b"<b:Sources><unclosed>")
    assert wordnative.load_sources(docx) == {}


def test_load_sources_no_customxml_at_all(tmp_path: Path):
    docx = tmp_path / "test.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", b"<w:document/>")
    assert wordnative.load_sources(docx) == {}


# --- parse_instruction (tag resolution against a sources map) ---------------


def test_parse_instruction_resolves_known_tags():
    sources = {
        "Smi20": RefEntry(key="", entry_type="article", title="A", raw_id="Smi20"),
        "Doe19": RefEntry(key="", entry_type="book", title="B", raw_id="Doe19"),
    }
    entries = wordnative.parse_instruction("CITATION Smi20 \\l 1033 \\m Doe19 \\l 1033", sources)
    assert [e.title for e in entries] == ["A", "B"]


def test_parse_instruction_unknown_tag_skipped_not_crashed():
    sources = {"Smi20": RefEntry(key="", entry_type="article", title="A", raw_id="Smi20")}
    entries = wordnative.parse_instruction("CITATION Ghost99 \\l 1033", sources)
    assert entries == []


def test_parse_instruction_empty_sources_map_never_crashes():
    assert wordnative.parse_instruction("CITATION Smi20 \\l 1033", {}) == []
