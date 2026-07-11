"""Graceful degradation: malformed/unknown field data never crashes (item 13).

Builds a small ad-hoc .docx (inline OOXML, not a named fixtures/make_*.py
script -- this is a narrow regression case, not a reusable feature fixture)
with one well-formed Zotero citation and one malformed EndNote citation
(truncated/unclosed embedded XML) side by side, and drives it all the way
through :func:`~latextify.emit.project.emit_project` -- the same integration
surface as the through-compile tests -- to prove the malformed field:

* never raises anywhere in the extraction -> sentinel -> emit pipeline,
* still contributes zero bib entries and zero resolved keys, and
* surfaces as an ``EmitWarning`` + ``[UNRESOLVED CITATION]`` placeholder in
  the body, reusing the same degradation path item 24 built for any
  citation with no matching keys -- rather than a parallel warning type.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from latextify.citations.fields import extract_field_citations
from latextify.emit.project import emit_project

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

ZOTERO_PAYLOAD = {
    "citationItems": [
        {
            "id": 1,
            "itemData": {
                "id": 1,
                "type": "article-journal",
                "title": "Well formed reference",
                "author": [{"family": "Doe", "given": "Jane"}],
                "issued": {"date-parts": [[2022]]},
            },
        }
    ],
}


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _instr_run(text: str) -> str:
    return f'<w:r><w:instrText xml:space="preserve">{_xml_escape(text)}</w:instrText></w:r>'


def _fldchar(char_type: str) -> str:
    return f'<w:r><w:fldChar w:fldCharType="{char_type}"/></w:r>'


def _text_run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def _field(instr: str, result: str) -> str:
    return "".join(
        [
            _fldchar("begin"),
            _instr_run(instr),
            _fldchar("separate"),
            _text_run(result),
            _fldchar("end"),
        ]
    )


def _build_docx(out_path: Path) -> None:
    zotero_instr = " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(ZOTERO_PAYLOAD) + " "
    # Truncated/unclosed EndNote XML: not valid XML either raw or unescaped.
    malformed_instr = (
        " ADDIN EN.CITE <EndNote><Cite><record><titles><title>Unterminated "
    )
    zotero_field = _field(zotero_instr, "[1]")
    malformed_field = _field(malformed_instr, "[2]")

    body = "".join(
        [
            "<w:p>",
            _text_run("A well-formed reference "),
            zotero_field,
            _text_run(" and a malformed one "),
            malformed_field,
            _text_run(" appear together."),
            "</w:p>",
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>',
        ]
    )
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


def test_malformed_field_extracted_without_crash(tmp_path: Path):
    docx = tmp_path / "malformed.docx"
    _build_docx(docx)

    result = extract_field_citations(docx)
    # Only the well-formed Zotero entry makes it into references.bib.
    assert len(result.entries) == 1
    assert result.entries[0].title == "Well formed reference"

    # Both citations are still recorded in document order; the malformed one
    # simply resolves to zero keys instead of raising.
    assert [c.index for c in result.citations] == [0, 1]
    assert result.citations[0].keys != ()
    assert result.citations[1].keys == ()
    assert result.citations[1].source == "endnote"


def test_malformed_field_degrades_to_emit_warning_not_crash(tmp_path: Path):
    docx = tmp_path / "malformed.docx"
    _build_docx(docx)

    # emit_project must complete without raising despite the malformed field.
    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    body = result.body_tex_path.read_text(encoding="utf-8")
    assert "\\cite{" in body  # the well-formed citation still links
    assert "[UNRESOLVED CITATION]" in body  # the malformed one degrades visibly
    assert any("unresolved citation" in w.message for w in result.warnings)
