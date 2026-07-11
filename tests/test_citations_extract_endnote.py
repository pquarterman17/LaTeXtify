"""End-to-end field-code extraction on the hand-crafted endnote_cited.docx.

Mirrors test_citations_extract.py's pattern for the Zotero/Mendeley fixture
(plan item 7); this is item 13's EndNote extractor exercised through the
same shared fields.py walker -> bib.py path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from latextify.citations.bib import entries_to_bib
from latextify.citations.fields import extract_field_citations

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "endnote_cited.docx"


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_endnote_cited", FIXTURE_DIR / "make_endnote_cited.py"
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
    assert keys == {
        "feynman1969quantum",
        "pathria1972statistical",
        "turing1950scalable",
        "wilczek1982topological",
        "devoret2013superconducting",
    }


def test_entry_types_and_dois(result):
    by_key = {e.key: e for e in result.entries}
    assert by_key["feynman1969quantum"].entry_type == "article"
    assert by_key["feynman1969quantum"].doi == "10.1103/PhysRev.1969.1"
    assert by_key["pathria1972statistical"].entry_type == "book"
    assert by_key["turing1950scalable"].entry_type == "inproceedings"
    assert by_key["wilczek1982topological"].entry_type == "incollection"
    assert by_key["devoret2013superconducting"].entry_type == "article"
    assert by_key["devoret2013superconducting"].doi == "10.1038/nphys1234"


def test_authors_and_fields_mapped(result):
    by_key = {e.key: e for e in result.entries}
    feynman = by_key["feynman1969quantum"]
    assert [a.family for a in feynman.authors] == ["Feynman", "Gell-Mann"]
    assert feynman.container_title == "Physical Review"
    assert feynman.volume == "1"
    assert feynman.pages == "1-10"


def test_secondary_title_fallback_reaches_bib_entry(result):
    turing = next(e for e in result.entries if e.key == "turing1950scalable")
    assert turing.container_title == "Proceedings of Computing History"


def test_citations_in_document_order(result):
    assert [c.index for c in result.citations] == [0, 1, 2, 3]
    assert [c.source for c in result.citations] == ["endnote"] * 4


def test_multi_item_citation_has_two_keys(result):
    multi = result.citations[1]
    assert multi.keys == ("pathria1972statistical", "turing1950scalable")


def test_nested_inner_citation_recovered(result):
    nested = result.citations[2]
    assert nested.keys == ("wilczek1982topological",)


def test_double_html_encoded_citation_recovered(result):
    """The 4th citation's field instruction has an extra HTML-entity layer."""
    encoded = result.citations[3]
    assert encoded.keys == ("devoret2013superconducting",)


def test_bib_contains_every_key(result):
    bib = entries_to_bib(result.entries)
    for entry in result.entries:
        assert f"{{{entry.key}," in bib
