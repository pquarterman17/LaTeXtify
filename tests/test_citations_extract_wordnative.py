"""End-to-end field-code extraction on the hand-crafted wordnative_cited.docx.

Mirrors test_citations_extract.py's pattern; this is item 13's Word-native
extractor -- a genuine CITATION field nested inside a w:sdt citation content
control, resolved against customXml/item1.xml's b:Sources -- exercised
through the same shared fields.py walker.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from latextify.citations.bib import entries_to_bib
from latextify.citations.fields import extract_field_citations

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "wordnative_cited.docx"


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_wordnative_cited", FIXTURE_DIR / "make_wordnative_cited.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.build()


@pytest.fixture(scope="module")
def result():
    _ensure_fixture()
    return extract_field_citations(DOCX)


def test_all_references_extracted(result):
    keys = {e.key for e in result.entries}
    assert keys == {"smith2018spinorbit", "anderson1984basic", "turing1950computing"}


def test_entry_types(result):
    by_key = {e.key: e for e in result.entries}
    assert by_key["smith2018spinorbit"].entry_type == "article"
    assert by_key["anderson1984basic"].entry_type == "book"
    assert by_key["turing1950computing"].entry_type == "inproceedings"


def test_fields_mapped_from_b_source(result):
    smith = next(e for e in result.entries if e.key == "smith2018spinorbit")
    assert smith.authors[0].family == "Smith"
    assert smith.authors[0].given == "Alice"
    assert smith.container_title == "Nano Letters"
    assert smith.volume == "18"
    assert smith.pages == "4521-4527"
    assert smith.source == "wordnative"


def test_corporate_and_conference_container_mapped(result):
    turing = next(e for e in result.entries if e.key == "turing1950computing")
    assert turing.container_title == "Mind Philosophy Symposium"


def test_citations_in_document_order(result):
    assert [c.index for c in result.citations] == [0, 1]
    assert [c.source for c in result.citations] == ["wordnative", "wordnative"]


def test_single_citation_sdt_resolves_one_key(result):
    assert result.citations[0].keys == ("smith2018spinorbit",)


def test_multi_citation_sdt_resolves_ordered_keys(result):
    assert result.citations[1].keys == ("anderson1984basic", "turing1950computing")


def test_bib_contains_every_key(result):
    bib = entries_to_bib(result.entries)
    for entry in result.entries:
        assert f"{{{entry.key}," in bib
