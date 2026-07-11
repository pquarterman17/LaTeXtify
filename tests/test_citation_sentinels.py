"""Citation-sentinel preprocessing: index alignment, nesting, edge cases.

Proves the sentinel planted for the i-th citation field pairs with the
``Citation`` whose ``.index`` is i -- the SAME document-order walk
``extract_field_citations`` uses -- so the emitter can swap ``ZZLTXCITE<i>ZZ``
for that citation's ``\\cite{...}``. The nested-field case (a Zotero citation
inside a ``PAGEREF``) is the one that would desynchronize a naive second walk.
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pypandoc

from latextify.citations.fields import extract_field_citations
from latextify.ingest.citation_sentinels import (
    SENTINEL_RE,
    plant_citation_sentinels,
    sentinel_for,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "zotero_cited.docx"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)

_BEGIN = '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
_SEP = '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
_END = '<w:r><w:fldChar w:fldCharType="end"/></w:r>'


def _instr(text: str) -> str:
    return f'<w:r><w:instrText xml:space="preserve">{text}</w:instrText></w:r>'


def _text(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _build_docx(path: Path, body: str) -> Path:
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("word/document.xml", doc)
    return path


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_zotero_cited", FIXTURE_DIR / "make_zotero_cited.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.build()


def _document_xml(docx: Path) -> str:
    with zipfile.ZipFile(docx) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _latex(docx: Path) -> str:
    return pypandoc.convert_file(str(docx), to="latex", format="docx")


def test_sentinel_for_format():
    assert sentinel_for(0) == "ZZLTXCITE0ZZ"
    assert sentinel_for(12) == "ZZLTXCITE12ZZ"
    assert SENTINEL_RE.fullmatch("ZZLTXCITE12ZZ").group(1) == "12"


def test_no_citation_fields_passes_through_unchanged(tmp_path):
    # A document whose only field is a non-citation PAGEREF: no copy is made.
    body = "<w:p>" + _BEGIN + _instr(" PAGEREF _Ref1 \\h ") + _SEP + _text("2") + _END + "</w:p>"
    docx = _build_docx(tmp_path / "nocite.docx", body)
    result = plant_citation_sentinels(docx, tmp_path / "work")
    assert result == docx  # same path, untouched
    assert "ZZLTXCITE" not in _document_xml(result)


def test_indices_align_with_citation_list_including_nested_field(tmp_path):
    _ensure_fixture()
    citations = extract_field_citations(DOCX).citations
    assert [c.index for c in citations] == [0, 1, 2, 3]

    prepared = plant_citation_sentinels(DOCX, tmp_path / "work")
    xml = _document_xml(prepared)

    # Exactly one sentinel per citation, each matching its 0-based index.
    planted = SENTINEL_RE.findall(xml)
    assert sorted(int(i) for i in planted) == [c.index for c in citations]

    latex = _latex(prepared)
    # Document order: sentinels appear in ascending citation-index order.
    positions = [latex.index(sentinel_for(c.index)) for c in citations]
    assert positions == sorted(positions)

    # The nested citation (index 2) replaced the INNER field's result; the
    # non-citation OUTER PAGEREF result ("Section 2") is preserved and follows
    # it -- proving the walk targeted the inner field, not the outer one.
    assert "ZZLTXCITE2ZZSection 2" in latex


def test_complex_field_without_separate_still_gets_a_sentinel(tmp_path):
    # A citation field with no 'separate' (result absent): a run is inserted.
    body = (
        "<w:p>"
        + _text("Before ")
        + _BEGIN
        + _instr(" ADDIN ZOTERO_ITEM CSL_CITATION {} ")
        + _END
        + _text(" after")
        + "</w:p>"
    )
    docx = _build_docx(tmp_path / "nosep.docx", body)
    prepared = plant_citation_sentinels(docx, tmp_path / "work")
    assert prepared != docx
    assert "ZZLTXCITE0ZZ" in _latex(prepared)


def test_fldsimple_result_runs_are_replaced(tmp_path):
    body = (
        '<w:p>' + _text("See ")
        + '<w:fldSimple w:instr=" ADDIN CSL_CITATION {} ">'
        + _text("[9]")
        + "</w:fldSimple>"
        + _text(" here")
        + "</w:p>"
    )
    docx = _build_docx(tmp_path / "simple.docx", body)
    prepared = plant_citation_sentinels(docx, tmp_path / "work")
    latex = _latex(prepared)
    assert "ZZLTXCITE0ZZ" in latex
    assert "[9]" not in latex  # cached display text was replaced
