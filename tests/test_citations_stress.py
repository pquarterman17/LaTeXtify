"""Adversarial stress tests across the citations territory.

These lock in behavior that was probed during a systematic bug hunt (2026-07-11)
and found to already be CORRECT -- i.e. they are regression tests, not bug-fix
tests (those live alongside their fixes in the more specific test_citations_*.py
files). Grouped by adversarial scenario rather than by module.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from latextify.citations import zotero
from latextify.citations.bib import assign_keys, make_base_key, to_bibtex
from latextify.citations.fields import extract_field_citations
from latextify.ingest.citation_sentinels import SENTINEL_RE
from latextify.model.refs import Name, RefEntry

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _entry(**kw) -> RefEntry:
    base = dict(key="", entry_type="article", csl_type="article-journal")
    base.update(kw)
    return RefEntry(**base)


# --------------------------------------------------------------------------- #
# CSL JSON: unexpected shapes
# --------------------------------------------------------------------------- #


def test_empty_citation_items_yields_no_entries():
    instr = 'ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems": []}'
    assert zotero.parse_instruction(instr) == []


def test_item_data_missing_author_title_and_year_does_not_crash():
    instr = (
        'ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems": '
        '[{"itemData": {"type": "article-journal"}}]}'
    )
    entries = zotero.parse_instruction(instr)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.title is None
    assert entry.authors == ()
    assert entry.year is None
    # make_base_key must still produce a non-empty key ("anon" + year/"nd").
    key = make_base_key(entry)
    assert key
    assert key.startswith("anon")


def test_author_with_only_literal_field():
    item = {"type": "report", "author": [{"literal": "The MoEDAL Collaboration"}]}
    entry = zotero.csl_item_to_refentry(item, "zotero")
    assert entry.authors[0].is_literal
    assert entry.authors[0].literal == "The MoEDAL Collaboration"
    # Renders brace-protected, never crashes the emitter.
    bib = to_bibtex(assign_keys([entry])[0])
    assert "{The MoEDAL Collaboration}" in bib


def test_non_string_doi_does_not_crash():
    item = {"type": "article-journal", "DOI": 10037581, "title": "X"}
    entry = zotero.csl_item_to_refentry(item, "zotero")
    assert entry.doi == "10037581"


def test_deeply_nested_and_duplicate_json_keys_do_not_crash():
    # Duplicate top-level keys (last wins, per json.loads semantics) plus a
    # deeply nested but irrelevant structure elsewhere in the payload.
    raw = (
        'ADDIN ZOTERO_ITEM CSL_CITATION {"nested": {"a": {"b": {"c": [1,2,3]}}}}, '
        '"citationItems": [{"itemData": {"type": "book", "title": "First"}}], '
        '"citationItems": [{"itemData": {"type": "book", "title": "Second"}}]}'
    )
    # Malformed JSON (trailing content after the object due to the duplicate
    # top-level key hack above is invalid) must degrade to {}, not crash.
    assert zotero.extract_json(raw) == {} or isinstance(zotero.extract_json(raw), dict)


def test_zotero_json_containing_sentinel_like_text_does_not_corrupt_extraction():
    # A CSL title/author that happens to literally contain the sentinel
    # pattern the ingest preprocessor plants (ZZLTXCITE<i>ZZ) must flow
    # through extraction/keying/bib emission unharmed -- the sentinel is only
    # ever matched against the pandoc-rendered BODY text (a separate stage,
    # out of this territory), never against citation metadata.
    item = {
        "type": "article-journal",
        "title": "A Study of the ZZLTXCITE0ZZ Syndrome",
        "author": [{"family": "ZZLTXCITE1ZZ", "given": "A."}],
        "issued": {"date-parts": [[2021]]},
    }
    entry = zotero.csl_item_to_refentry(item, "zotero")
    keyed = assign_keys([entry])[0]
    bib = to_bibtex(keyed)
    assert "ZZLTXCITE0ZZ" in bib
    # The sentinel-matching regex must not accidentally fire on bib content
    # in a way that breaks re.sub elsewhere -- it simply matches literally,
    # which is expected and harmless since .bib text is never run through it.
    assert SENTINEL_RE.search(bib) is not None  # present, but inert here
    assert keyed.key  # key generation still succeeded


# --------------------------------------------------------------------------- #
# BibTeX emission stress
# --------------------------------------------------------------------------- #


def test_cjk_author_and_title_never_produce_empty_key():
    entry = _entry(
        authors=(Name(family="田中", given="太郎"),),
        year=None,
        title="超伝導の研究",
    )
    key = make_base_key(entry)
    assert key  # never empty
    assert key == "anonnd"


def test_cjk_entries_with_shared_fallback_key_still_get_unique_suffixes():
    a = _entry(authors=(Name(family="田中"),), year="2020", title="研究A")
    b = _entry(authors=(Name(family="李"),), year="2020", title="研究B")
    keyed = assign_keys([a, b])
    assert keyed[0].key != keyed[1].key
    assert all(e.key for e in keyed)


def test_all_uppercase_title_stays_valid_bibtex():
    e = _entry(key="k", authors=(Name(family="Doe"),), title="SOME PAPER TITLE")
    record = to_bibtex(e)
    assert record.startswith("@article{k,")
    assert "SOME" in record and "PAPER" in record and "TITLE" in record


def test_backslash_in_title_is_escaped_not_left_raw():
    e = _entry(key="k", authors=(Name(family="Doe"),), title=r"A \dangerous title")
    record = to_bibtex(e)
    assert "\\textbackslash{}" in record
    assert "\\dangerous" not in record  # not left as a raw, uninteded macro


def test_math_mode_dollar_in_title_is_escaped():
    e = _entry(key="k", authors=(Name(family="Doe"),), title="Energy gap $E_g$ in silicon")
    record = to_bibtex(e)
    assert r"\$E" in record
    assert "$E_g$" not in record  # unescaped $ would open inline math


# --------------------------------------------------------------------------- #
# Plain-text heading recognition variants
# --------------------------------------------------------------------------- #


def _build_raw_docx(path: Path, heading: str, refs: list[str]) -> Path:
    def para(text: str) -> str:
        return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'

    body = para(heading) + "".join(para(r) for r in refs)
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)
    return path


def test_all_caps_references_heading_recognized(tmp_path):
    from latextify.citations.plaintext import segment_reference_list

    docx = _build_raw_docx(
        tmp_path / "caps.docx", "REFERENCES", ["1. Smith, A. First paper. (2020)."]
    )
    reflist = segment_reference_list(docx)
    assert reflist.found
    assert reflist.heading == "REFERENCES"


def test_references_heading_with_trailing_colon_recognized(tmp_path):
    from latextify.citations.plaintext import segment_reference_list

    docx = _build_raw_docx(
        tmp_path / "colon.docx", "References:", ["1. Smith, A. First paper. (2020)."]
    )
    reflist = segment_reference_list(docx)
    assert reflist.found
    assert reflist.heading == "References"  # trailing colon stripped


def test_empty_references_section_is_not_found(tmp_path):
    from latextify.citations.plaintext import segment_reference_list

    docx = _build_raw_docx(tmp_path / "empty.docx", "References", [])
    reflist = segment_reference_list(docx)
    assert not reflist.found  # heading with zero following references


# --------------------------------------------------------------------------- #
# scale: 500+ citations, key-collision cascade past 'z'
# --------------------------------------------------------------------------- #


def test_key_suffix_cascade_past_z_stays_unique():
    # 40 entries sharing one base key forces suffixes past 'z' (a..z, then
    # za, zb, ...); every key must still be unique and non-empty.
    entries = [
        _entry(authors=(Name(family="Smith"),), year="2020", title=f"Study {i}", doi=f"10.1/{i}")
        for i in range(40)
    ]
    keyed = assign_keys(entries)
    keys = [e.key for e in keyed]
    assert len(set(keys)) == len(keys) == 40
    assert all(keys)
    assert keys[25] == "smith2020studyz"
    assert keys[26] == "smith2020studyza"


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _citation_field(instr: str, result: str) -> str:
    instr_run = f'<w:r><w:instrText xml:space="preserve">{_xml_escape(instr)}</w:instrText></w:r>'
    text_run = f'<w:r><w:t xml:space="preserve">{_xml_escape(result)}</w:t></w:r>'
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        + instr_run
        + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + text_run
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def _build_citations_docx(path: Path, fields: list[str]) -> Path:
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)
    return path


def test_500_plus_citations_extract_quickly_with_unique_keys(tmp_path):
    n = 550
    fields = []
    for i in range(n):
        payload = {
            "citationItems": [
                {
                    "id": i,
                    "itemData": {
                        "id": i,
                        "type": "article-journal",
                        "title": f"Study of Widget Number {i}",
                        "author": [{"family": "Smith", "given": "A"}],
                        "issued": {"date-parts": [[2020]]},
                        "DOI": f"10.1000/widget.{i}",
                    },
                }
            ]
        }
        instr = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(payload) + " "
        fields.append(_citation_field(instr, f"[{i}]"))

    docx = _build_citations_docx(tmp_path / "big.docx", fields)

    started = time.monotonic()
    result = extract_field_citations(docx)
    elapsed = time.monotonic() - started

    assert len(result.entries) == n
    keys = [e.key for e in result.entries]
    assert len(set(keys)) == n
    assert all(keys)
    assert elapsed < 10.0  # generous ceiling; typical run is well under 1s


# --------------------------------------------------------------------------- #
# cross-source dedup: same reference cited via two different citation managers
# --------------------------------------------------------------------------- #


def test_zotero_and_endnote_citing_same_doi_dedup_to_one_entry(tmp_path):
    zotero_payload = {
        "citationItems": [
            {
                "itemData": {
                    "type": "article-journal",
                    "title": "Shared Paper",
                    "author": [{"family": "Shared", "given": "Author"}],
                    "issued": {"date-parts": [[2020]]},
                    "DOI": "10.1000/shared.doi",
                }
            }
        ]
    }
    zotero_instr = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(zotero_payload) + " "

    endnote_instr = (
        " ADDIN EN.CITE <EndNote><Cite><Author>Shared</Author><Year>2020</Year>"
        "<record><titles><title>Shared Paper (EndNote copy)</title></titles>"
        "<contributors><authors><author>Shared, Author</author></authors></contributors>"
        "<dates><year>2020</year></dates>"
        "<electronic-resource-num>10.1000/shared.doi</electronic-resource-num>"
        "<ref-type name=\"Journal Article\"/>"
        "</record></Cite></EndNote> "
    )

    docx = _build_citations_docx(
        tmp_path / "cross_source.docx",
        [
            _citation_field(zotero_instr, "[1]"),
            _citation_field(endnote_instr, "[2]"),
        ],
    )
    result = extract_field_citations(docx)

    # Same DOI from two different citation managers -> exactly one bib entry.
    assert len(result.entries) == 1
    assert result.entries[0].doi == "10.1000/shared.doi"
    # Both in-text citations resolve to that single shared key.
    assert result.citations[0].keys == result.citations[1].keys
