"""Generate ``zotero_cited.docx`` — a hand-crafted OOXML citation fixture.

python-docx cannot emit Word *complex* fields (the ``w:fldChar`` / ``w:instrText``
run machinery that carries Zotero/Mendeley CSL JSON), so we assemble the OPC
package and ``word/document.xml`` by hand. The document exercises, in order:

1. A Zotero article-journal citation (two authors, DOI) whose instruction is
   SPLIT across three ``w:instrText`` runs (concatenation requirement).
2. A single Zotero field with TWO ``citationItems`` (a book + a
   paper-conference) — the multi-item case.
3. A NESTED field: an outer ``PAGEREF`` field containing an inner Zotero
   chapter citation — the walker must recover the inner citation and skip the
   non-citation outer field.
4. A Mendeley ``ADDIN CSL_CITATION`` article citation.

Run directly to (re)write the fixture next to this script::

    python tests/fixtures/make_zotero_cited.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DEFAULT_OUT = Path(__file__).with_name("zotero_cited.docx")

# --- CSL JSON payloads -------------------------------------------------------

ARTICLE = {
    "citationID": "zart1",
    "properties": {"formattedCitation": "[1]", "plainCitation": "[1]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 101,
            "uris": ["http://zotero.org/users/1/items/AAAA1111"],
            "itemData": {
                "id": 101,
                "type": "article-journal",
                "title": "Quantum transport in GaAs heterostructures",
                "container-title": "Physical Review B",
                "DOI": "10.1103/PhysRevB.101.045123",
                "volume": "101",
                "issue": "4",
                "page": "045123",
                "author": [
                    {"family": "Müller", "given": "Hans"},
                    {"family": "Nyström", "given": "Erik"},
                ],
                "issued": {"date-parts": [[2020, 1, 15]]},
            },
        }
    ],
    "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
}

MULTI = {
    "citationID": "zmulti2",
    "properties": {"formattedCitation": "[2, 3]", "plainCitation": "[2, 3]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 102,
            "uris": ["http://zotero.org/users/1/items/BBBB2222"],
            "itemData": {
                "id": 102,
                "type": "book",
                "title": "Introduction to Solid State Physics",
                "author": [{"family": "Kittel", "given": "Charles"}],
                "publisher": "Wiley",
                "ISBN": "978-0-471-41526-8",
                "issued": {"date-parts": [[2005]]},
            },
        },
        {
            "id": 103,
            "uris": ["http://zotero.org/users/1/items/CCCC3333"],
            "itemData": {
                "id": 103,
                "type": "paper-conference",
                "title": "Scalable qubit control electronics",
                "container-title": (
                    "Proceedings of the International Conference on Quantum Computing"
                ),
                "author": [
                    {"family": "Smith", "given": "Alice"},
                    {"family": "Zhang", "given": "Wei"},
                ],
                "page": "12-19",
                "DOI": "10.1109/QC.2019.00012",
                "issued": {"date-parts": [[2019]]},
            },
        },
    ],
}

CHAPTER = {
    "citationID": "zchap3",
    "properties": {"formattedCitation": "[4]", "plainCitation": "[4]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 104,
            "uris": ["http://zotero.org/users/1/items/DDDD4444"],
            "itemData": {
                "id": 104,
                "type": "chapter",
                "title": "Topological Insulators",
                "container-title": "Handbook of Condensed Matter",
                "author": [{"family": "García", "given": "Lucía"}],
                "publisher": "Springer",
                "page": "200-240",
                "issued": {"date-parts": [[2018]]},
            },
        }
    ],
}

MENDELEY = {
    "citationID": "MENDELEY_CITATION_5",
    "properties": {"noteIndex": 0},
    "citationItems": [
        {
            "id": "ITEM-1",
            "itemData": {
                "id": "ITEM-1",
                "type": "article-journal",
                "title": "Superconductivity at high pressure",
                "container-title": "Nature",
                "author": [{"family": "Smith", "given": "Alice"}],
                "volume": "590",
                "page": "55-60",
                "DOI": "10.1038/s41586-021-00001",
                "issued": {"date-parts": [["2021"]]},
            },
        }
    ],
    "mendeley": {"formattedCitation": "[5]", "plainTextFormattedCitation": "[5]"},
}


# --- OOXML building helpers --------------------------------------------------


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _instr_run(text: str) -> str:
    return (
        f'<w:r><w:instrText xml:space="preserve">{_xml_escape(text)}</w:instrText></w:r>'
    )


def _fldchar(char_type: str) -> str:
    return f'<w:r><w:fldChar w:fldCharType="{char_type}"/></w:r>'


def _text_run(text: str) -> str:
    return f"<w:r><w:t xml:space=\"preserve\">{_xml_escape(text)}</w:t></w:r>"


def _field(instr_chunks: list[str], result: str) -> str:
    """A complex field: begin, one+ instrText runs, separate, result, end."""
    runs = [_fldchar("begin")]
    runs += [_instr_run(chunk) for chunk in instr_chunks]
    runs.append(_fldchar("separate"))
    runs.append(_text_run(result))
    runs.append(_fldchar("end"))
    return "".join(runs)


def _zotero_instruction(payload: dict) -> str:
    return " ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(payload, ensure_ascii=False) + " "


def _mendeley_instruction(payload: dict) -> str:
    return " ADDIN CSL_CITATION " + json.dumps(payload, ensure_ascii=False) + " "


def _split_three(text: str) -> list[str]:
    third = max(1, len(text) // 3)
    return [text[:third], text[third : 2 * third], text[2 * third :]]


def _nested_field(inner_payload: dict, inner_result: str, outer_result: str) -> str:
    """Outer PAGEREF field with an inner Zotero citation field nested inside."""
    runs = [_fldchar("begin"), _instr_run(r" PAGEREF _Ref0001 \h ")]
    runs.append(_field([_zotero_instruction(inner_payload)], inner_result))
    runs.append(_fldchar("separate"))
    runs.append(_text_run(outer_result))
    runs.append(_fldchar("end"))
    return "".join(runs)


def _paragraph(*inner: str) -> str:
    return "<w:p>" + "".join(inner) + "</w:p>"


def build_document_xml() -> str:
    article_field = _field(_split_three(_zotero_instruction(ARTICLE)), "[1]")
    multi_field = _field([_zotero_instruction(MULTI)], "[2, 3]")
    nested = _nested_field(CHAPTER, "[4]", "Section 2")
    mendeley_field = _field([_mendeley_instruction(MENDELEY)], "[5]")

    body = "".join(
        [
            _paragraph(
                _text_run("Charge transport has been widely studied "),
                article_field,
                _text_run("."),
            ),
            _paragraph(
                _text_run("Foundational treatments "),
                multi_field,
                _text_run(" cover the basics."),
            ),
            _paragraph(
                _text_run("As discussed in "),
                nested,
                _text_run(", the surface states matter."),
            ),
            _paragraph(
                _text_run("Recent high-pressure work "),
                mendeley_field,
                _text_run(" is notable."),
            ),
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>',
        ]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def build(out_path: Path | str = DEFAULT_OUT) -> Path:
    """Write the .docx fixture and return its path."""
    out_path = Path(out_path)
    document_xml = build_document_xml()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("word/document.xml", document_xml)
    return out_path


if __name__ == "__main__":
    written = build()
    print(f"wrote {written}")
