"""End-to-end field-code extraction on the hand-crafted zotero_cited.docx.

This is the integration test through *extraction* (walker -> parsers -> bib).
The integration through *compile* lives in test_citations_compile_stub.py and
is skipped until the sibling body/emitter/compile items land.
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from latextify.citations.bib import entries_to_bib
from latextify.citations.fields import extract_field_citations

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "zotero_cited.docx"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field(instr: str, result: str) -> str:
    instr_run = f'<w:r><w:instrText xml:space="preserve">{_xml_escape(instr)}</w:instrText></w:r>'
    text_run = f'<w:r><w:t xml:space="preserve">{_xml_escape(result)}</w:t></w:r>'
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + instr_run
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + text_run
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def _build_docx(out_path: Path, fields: list[str]) -> Path:
    body = "".join(f"<w:p>{f}</w:p>" for f in fields)
    body += '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)
    return out_path


def test_two_distinct_citations_with_wholly_empty_item_data_are_not_merged(tmp_path):
    """Two malformed Zotero fields with NO identifying data must stay distinct.

    itemData missing author, title, year, DOI, AND id gives every dedup
    signal an empty value; two genuinely different (if catastrophically
    malformed) citations must not collapse into a single shared reference --
    that would silently point one of the two in-text citations at the wrong
    (or a nonexistent) source.
    """
    payload_a = {"citationItems": [{"itemData": {"type": "article-journal"}}]}
    payload_b = {"citationItems": [{"itemData": {"type": "book"}}]}
    instr_a = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(payload_a) + " "
    instr_b = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(payload_b) + " "
    docx = _build_docx(
        tmp_path / "empty_items.docx", [_field(instr_a, "[1]"), _field(instr_b, "[2]")]
    )

    result = extract_field_citations(docx)

    assert len(result.entries) == 2  # not merged into one
    assert len({e.key for e in result.entries}) == 2  # unique, non-empty keys
    assert all(e.key for e in result.entries)
    assert [c.index for c in result.citations] == [0, 1]
    assert result.citations[0].keys != result.citations[1].keys


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_zotero_cited", FIXTURE_DIR / "make_zotero_cited.py"
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
        "muller2020quantum",
        "kittel2005introduction",
        "smith2019scalable",
        "garcia2018topological",
        "smith2021superconductivity",
    }


def test_entry_types_and_dois(result):
    by_key = {e.key: e for e in result.entries}
    assert by_key["muller2020quantum"].entry_type == "article"
    assert by_key["muller2020quantum"].doi == "10.1103/PhysRevB.101.045123"
    assert by_key["kittel2005introduction"].entry_type == "book"
    assert by_key["smith2019scalable"].entry_type == "inproceedings"
    assert by_key["garcia2018topological"].entry_type == "incollection"
    assert by_key["smith2021superconductivity"].entry_type == "article"


def test_unicode_author_names_preserved_in_entry(result):
    muller = next(e for e in result.entries if e.key == "muller2020quantum")
    assert muller.authors[0].family == "Müller"
    assert muller.authors[1].family == "Nyström"


def test_citations_in_document_order(result):
    assert [c.index for c in result.citations] == [0, 1, 2, 3]


def test_multi_item_citation_has_two_keys(result):
    multi = result.citations[1]
    assert multi.keys == ("kittel2005introduction", "smith2019scalable")


def test_nested_inner_citation_recovered(result):
    nested = result.citations[2]
    assert nested.source == "zotero"
    assert nested.keys == ("garcia2018topological",)


def test_mendeley_citation_detected(result):
    mend = result.citations[3]
    assert mend.source == "mendeley"
    assert mend.keys == ("smith2021superconductivity",)


def test_all_citation_keys_resolve_to_entries(result):
    """Every %%CITE anchor key must have a matching bib entry (pairing ready)."""
    entry_keys = {e.key for e in result.entries}
    for citation in result.citations:
        for key in citation.keys:
            assert key in entry_keys


def test_bib_contains_every_key(result):
    bib = entries_to_bib(result.entries)
    for entry in result.entries:
        assert f"{{{entry.key}," in bib
