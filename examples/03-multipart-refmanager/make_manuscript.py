"""Generate example 03: a multi-part submission with reference-manager citations.

The realistic "full submission" shape:

    main.docx        the manuscript, citations inserted by a reference manager
    supplement.docx  a separate Supplementary Material document
    paper.yaml       explicit title-page metadata (authors/affiliations)

Citations here are **Word field codes** — exactly what Zotero's or Mendeley's
"Cite While You Write" plugin embeds when you insert a citation. Each field
carries the full CSL-JSON record (authors, title, journal, DOI, year), so
LaTeXtify builds ``references.bib`` straight from the document with **no
Crossref lookup and no network**. There is deliberately no separate ``.bib``
or reference-manager *library file* here: LaTeXtify does not ingest a library
export — the metadata rides inside the document's field codes.

python-docx cannot author complex fields, so (like the test fixtures) this
script assembles ``word/document.xml`` by hand. One reference (Cornelissen
2015) is cited in BOTH documents to show that the shared ``references.bib``
de-duplicates it by DOI.

Regenerate with::

    python make_manuscript.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
MAIN_PATH = HERE / "main.docx"
SUPPLEMENT_PATH = HERE / "supplement.docx"
PAPER_YAML_PATH = HERE / "paper.yaml"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# --- Real CSL-JSON records (as a reference manager would embed them) ---------

CORNELISSEN = {
    "citationID": "cite_cornelissen",
    "properties": {"formattedCitation": "[1]", "plainCitation": "[1]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 1,
            "uris": ["http://zotero.org/users/1/items/CORN2015"],
            "itemData": {
                "id": 1,
                "type": "article-journal",
                "title": (
                    "Long-distance transport of magnon spin information in a "
                    "magnetic insulator at room temperature"
                ),
                "container-title": "Nature Physics",
                "DOI": "10.1038/nphys3465",
                "volume": "11",
                "page": "1022-1026",
                "author": [
                    {"family": "Cornelissen", "given": "L. J."},
                    {"family": "Liu", "given": "J."},
                    {"family": "Duine", "given": "R. A."},
                    {"family": "Ben Youssef", "given": "J."},
                    {"family": "van Wees", "given": "B. J."},
                ],
                "issued": {"date-parts": [[2015]]},
            },
        }
    ],
    "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
}

CHUMAK = {
    "citationID": "cite_chumak",
    "properties": {"formattedCitation": "[2]", "plainCitation": "[2]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 2,
            "itemData": {
                "id": 2,
                "type": "article-journal",
                "title": "Magnon spintronics",
                "container-title": "Nature Physics",
                "DOI": "10.1038/nphys3347",
                "volume": "11",
                "page": "453-461",
                "author": [
                    {"family": "Chumak", "given": "A. V."},
                    {"family": "Vasyuchka", "given": "V. I."},
                    {"family": "Serga", "given": "A. A."},
                    {"family": "Hillebrands", "given": "B."},
                ],
                "issued": {"date-parts": [[2015]]},
            },
        }
    ],
    "mendeley": {"formattedCitation": "[2]", "plainTextFormattedCitation": "[2]"},
}

KAJIWARA = {
    "citationID": "cite_kajiwara",
    "properties": {"formattedCitation": "[1]", "plainCitation": "[1]", "noteIndex": 0},
    "citationItems": [
        {
            "id": 3,
            "uris": ["http://zotero.org/users/1/items/KAJI2010"],
            "itemData": {
                "id": 3,
                "type": "article-journal",
                "title": (
                    "Transmission of electrical signals by spin-wave "
                    "interconversion in a magnetic insulator"
                ),
                "container-title": "Nature",
                "DOI": "10.1038/nature08876",
                "volume": "464",
                "page": "262-266",
                "author": [
                    {"family": "Kajiwara", "given": "Y."},
                    {"family": "Harii", "given": "K."},
                    {"family": "Takahashi", "given": "S."},
                ],
                "issued": {"date-parts": [[2010]]},
            },
        }
    ],
    "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
}


# --- OOXML complex-field helpers (mirrors tests/fixtures/make_zotero_cited.py) -

def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _instr_run(text: str) -> str:
    return f'<w:r><w:instrText xml:space="preserve">{_xml_escape(text)}</w:instrText></w:r>'


def _fldchar(char_type: str) -> str:
    return f'<w:r><w:fldChar w:fldCharType="{char_type}"/></w:r>'


def _text_run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def _field(instruction: str, result: str) -> str:
    """A Word complex field: begin, instrText, separate, displayed result, end."""
    return "".join([
        _fldchar("begin"), _instr_run(instruction), _fldchar("separate"),
        _text_run(result), _fldchar("end"),
    ])


def _zotero_field(payload: dict, result: str) -> str:
    return _field(" ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(payload) + " ", result)


def _mendeley_field(payload: dict, result: str) -> str:
    return _field(" ADDIN CSL_CITATION " + json.dumps(payload) + " ", result)


def _paragraph(*inner: str) -> str:
    return "<w:p>" + "".join(inner) + "</w:p>"


def _document_xml(*paragraphs: str) -> str:
    body = "".join(paragraphs) + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )


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


def _write_docx(path: Path, document_xml: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("word/document.xml", document_xml)


# --- The two manuscripts + the metadata sidecar ------------------------------

def _build_main() -> None:
    _write_docx(MAIN_PATH, _document_xml(
        _paragraph(
            _text_run("Magnons transport spin over long distances in magnetic "
                      "insulators "),
            _zotero_field(CORNELISSEN, "[1]"),
            _text_run(", a result that helped launch the field of magnon "
                      "spintronics "),
            _mendeley_field(CHUMAK, "[2]"),
            _text_run("."),
        ),
        _paragraph(
            _text_run("This manuscript revisits those non-local experiments and "
                      "extends them to thin-film devices."),
        ),
    ))


def _build_supplement() -> None:
    _write_docx(SUPPLEMENT_PATH, _document_xml(
        _paragraph(
            _text_run("Spin-wave interconversion in a magnetic insulator "),
            _zotero_field(KAJIWARA, "[1]"),
            _text_run(" underpins the injector/detector scheme used here."),
        ),
        _paragraph(
            _text_run("Full device-fabrication details supplement the main-text "
                      "measurements of magnon spin transport "),
            _zotero_field(CORNELISSEN, "[2]"),
            _text_run("."),
        ),
    ))


def _write_paper_yaml() -> None:
    # paper.yaml is the metadata sidecar for the MAIN document (the supplement
    # inherits "Supplementary Material: <title>" and the same author block).
    PAPER_YAML_PATH.write_text(
        "title: Non-local Magnon Spin Transport in Thin-Film Insulators\n"
        "affiliations:\n"
        "  - Institute for Spintronics, Example University, Springfield, USA\n"
        "  - National Laboratory for Materials, Metropolis, USA\n"
        "authors:\n"
        "  - name: Dana R. Leadauthor\n"
        "    affiliations: [1]\n"
        "    email: dana.leadauthor@example.edu\n"
        "    corresponding: true\n"
        "  - name: Evan S. Coauthor\n"
        "    affiliations: [1, 2]\n"
        "abstract: >-\n"
        "  We study non-local magnon spin transport in thin-film magnetic\n"
        "  insulators and provide supplementary fabrication details.\n"
        "keywords:\n"
        "  - magnon spintronics\n"
        "  - spin transport\n",
        encoding="utf-8",
    )


def build() -> Path:
    _build_main()
    _build_supplement()
    _write_paper_yaml()
    return MAIN_PATH


if __name__ == "__main__":
    build()
    print(f"wrote {MAIN_PATH}")
    print(f"wrote {SUPPLEMENT_PATH}")
    print(f"wrote {PAPER_YAML_PATH}")
