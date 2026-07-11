"""Generate ``wordnative_cited.docx`` — a hand-crafted OOXML Word-native
bibliography fixture.

Mirrors ``make_zotero_cited.py``'s hand-assembled OPC package, plus a
``customXml/item1.xml`` bibliography-sources part. Word's own References >
Insert Citation tool (no Zotero/Mendeley/EndNote plugin) stores each source
as a ``b:Source`` in ``customXml/item*.xml`` keyed by ``b:Tag``, and wraps
each in-text citation in a ``w:sdt`` content control marked with an empty
``<w:citation/>`` in ``w:sdtPr`` -- but the actual reference pointer is a
genuine Word field (``CITATION <Tag> \\l <lcid>``, same fldChar/instrText
machinery as every other citation source) nested inside ``w:sdtContent``.
See ``latextify/citations/wordnative.py``'s module docstring for the full
rationale.

The document exercises, in order:

1. A single-source citation (``CITATION Smi20 \\l 1033``) whose instruction
   is SPLIT across three ``w:instrText`` runs, wrapped in a citation ``w:sdt``.
2. A multi-source citation (``CITATION Kit05 \\l 1033 \\m Tur50 \\l 1033``),
   also wrapped in a citation ``w:sdt``.

``customXml/item1.xml`` defines three ``b:Source`` entries: ``Smi20``
(journal article), ``Kit05`` (book), ``Tur50`` (conference proceedings).

Run directly to (re)write the fixture next to this script::

    python tests/fixtures/make_wordnative_cited.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from latextify.citations.wordnative import B as BIB_NS

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DEFAULT_OUT = Path(__file__).with_name("wordnative_cited.docx")


# --- customXml/item1.xml: b:Sources ------------------------------------------

SOURCES_XML = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<b:Sources xmlns:b="{BIB_NS}" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" SelectedStyle="">'
    "<b:Source>"
    "<b:Tag>Smi20</b:Tag>"
    "<b:SourceType>JournalArticle</b:SourceType>"
    "<b:Title>Spin-orbit coupling in 2D materials</b:Title>"
    "<b:JournalName>Nano Letters</b:JournalName>"
    "<b:Year>2018</b:Year>"
    "<b:Volume>18</b:Volume>"
    "<b:Pages>4521-4527</b:Pages>"
    "<b:Author><b:Author><b:NameList>"
    "<b:Person><b:Last>Smith</b:Last><b:First>Alice</b:First></b:Person>"
    "</b:NameList></b:Author></b:Author>"
    "</b:Source>"
    "<b:Source>"
    "<b:Tag>Kit05</b:Tag>"
    "<b:SourceType>Book</b:SourceType>"
    "<b:Title>Basic Notions of Condensed Matter Physics</b:Title>"
    "<b:Year>1984</b:Year>"
    "<b:Publisher>Westview Press</b:Publisher>"
    "<b:Author><b:Author><b:NameList>"
    "<b:Person><b:Last>Anderson</b:Last><b:First>Philip</b:First><b:Middle>W.</b:Middle></b:Person>"
    "</b:NameList></b:Author></b:Author>"
    "</b:Source>"
    "<b:Source>"
    "<b:Tag>Tur50</b:Tag>"
    "<b:SourceType>ConferenceProceedings</b:SourceType>"
    "<b:Title>Computing Machinery and Intelligence</b:Title>"
    "<b:ConferenceName>Mind Philosophy Symposium</b:ConferenceName>"
    "<b:Year>1950</b:Year>"
    "<b:Author><b:Author><b:NameList>"
    "<b:Person><b:Last>Turing</b:Last><b:First>Alan</b:First></b:Person>"
    "</b:NameList></b:Author></b:Author>"
    "</b:Source>"
    "</b:Sources>"
).encode()


# --- OOXML building helpers (mirrors make_zotero_cited.py) ------------------


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _instr_run(text: str) -> str:
    return f'<w:r><w:instrText xml:space="preserve">{_xml_escape(text)}</w:instrText></w:r>'


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


def _split_three(text: str) -> list[str]:
    third = max(1, len(text) // 3)
    return [text[:third], text[third : 2 * third], text[2 * third :]]


def _sdt_citation(field_xml: str) -> str:
    """A citation content control: w:sdtPr/w:citation marker wrapping a field."""
    return (
        "<w:sdt><w:sdtPr><w:id w:val=\"1\"/><w:citation/></w:sdtPr>"
        "<w:sdtEndPr/>"
        f"<w:sdtContent>{field_xml}</w:sdtContent></w:sdt>"
    )


def _paragraph(*inner: str) -> str:
    return "<w:p>" + "".join(inner) + "</w:p>"


SINGLE_INSTR = " CITATION Smi20 \\l 1033 "
MULTI_INSTR = " CITATION Kit05 \\l 1033 \\m Tur50 \\l 1033 "


def build_document_xml() -> str:
    single_field = _field(_split_three(SINGLE_INSTR), "(Smith, 2018)")
    multi_field = _field([MULTI_INSTR], "(Anderson, 1984; Turing, 1950)")

    body = "".join(
        [
            _paragraph(
                _text_run("Spin-orbit effects have been reported "),
                _sdt_citation(single_field),
                _text_run("."),
            ),
            _paragraph(
                _text_run("Foundational treatments "),
                _sdt_citation(multi_field),
                _text_run(" cover the basics."),
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
        archive.writestr("customXml/item1.xml", SOURCES_XML)
    return out_path


if __name__ == "__main__":
    written = build()
    print(f"wrote {written}")
