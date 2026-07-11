"""Generate ``endnote_cited.docx`` — a hand-crafted OOXML EndNote fixture.

Mirrors ``make_zotero_cited.py``'s hand-assembled OPC package (python-docx
cannot author complex fields either). The document exercises, in order:

1. A single ``ADDIN EN.CITE`` journal-article citation whose instruction is
   SPLIT across three ``w:instrText`` runs (concatenation requirement).
2. A single EndNote field with TWO ``<Cite>`` elements (a book + a conference
   paper) — the multi-item case; the conference paper has no
   ``<periodical>``, so its journal falls back to ``<secondary-title>``.
3. A NESTED field: an outer ``PAGEREF`` field containing an inner EndNote
   book-section citation — the shared fields.py walker must recover it,
   exactly like the Zotero nested case.
4. A citation whose embedded ``<EndNote>...</EndNote>`` XML is wrapped in an
   EXTRA layer of HTML-entity encoding (some EndNote versions do this), to
   exercise the encoded-fragment fallback in ``endnote.py``.

Run directly to (re)write the fixture next to this script::

    python tests/fixtures/make_endnote_cited.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DEFAULT_OUT = Path(__file__).with_name("endnote_cited.docx")


# --- EndNote <Cite> XML fragments --------------------------------------------


def _cite_xml(
    recnum: int,
    ref_type: str,
    title: str,
    authors: list[str],
    year: str,
    journal: str | None = None,
    secondary_title: str | None = None,
    volume: str | None = None,
    pages: str | None = None,
    doi: str | None = None,
    display: str = "[1]",
) -> str:
    author_xml = "".join(f"<author>{a}</author>" for a in authors)
    journal_xml = f"<periodical><full-title>{journal}</full-title></periodical>" if journal else ""
    titles_xml = f"<titles><title>{title}</title>"
    if secondary_title:
        titles_xml += f"<secondary-title>{secondary_title}</secondary-title>"
    titles_xml += "</titles>"
    volume_xml = f"<volume>{volume}</volume>" if volume else ""
    pages_xml = f"<pages>{pages}</pages>" if pages else ""
    doi_xml = f"<electronic-resource-num>{doi}</electronic-resource-num>" if doi else ""
    first_author_family = authors[0].split(",")[0]
    return (
        "<Cite>"
        f"<Author>{first_author_family}</Author><Year>{year}</Year>"
        f"<RecNum>{recnum}</RecNum><DisplayText>{display}</DisplayText>"
        "<record>"
        f"<rec-number>{recnum}</rec-number>"
        f'<ref-type name="{ref_type}">17</ref-type>'
        f"<contributors><authors>{author_xml}</authors></contributors>"
        f"{titles_xml}{journal_xml}{volume_xml}{pages_xml}"
        f"<dates><year>{year}</year></dates>"
        f"{doi_xml}"
        "</record></Cite>"
    )


SINGLE_XML = "<EndNote>" + _cite_xml(
    1,
    "Journal Article",
    "Quantum electrodynamics of high-energy collisions",
    ["Feynman, Richard P.", "Gell-Mann, Murray"],
    "1969",
    journal="Physical Review",
    volume="1",
    pages="1-10",
    doi="10.1103/PhysRev.1969.1",
    display="[1]",
) + "</EndNote>"

MULTI_XML = (
    "<EndNote>"
    + _cite_xml(2, "Book", "Statistical Mechanics", ["Pathria, R. K."], "1972", display="[2, 3]")
    + _cite_xml(
        3,
        "Conference Paper",
        "Scalable EndNote parsing",
        ["Turing, Alan"],
        "1950",
        secondary_title="Proceedings of Computing History",
        display="[2, 3]",
    )
    + "</EndNote>"
)

CHAPTER_XML = "<EndNote>" + _cite_xml(
    4,
    "Book Section",
    "Topological classification of matter",
    ["Wilczek, Frank"],
    "1982",
    display="[4]",
) + "</EndNote>"

DOUBLE_ENCODED_SOURCE_XML = "<EndNote>" + _cite_xml(
    5,
    "Journal Article",
    "Superconducting qubit coherence times",
    ["Devoret, Michel"],
    "2013",
    journal="Nature Physics",
    volume="9",
    pages="300-305",
    doi="10.1038/nphys1234",
    display="[5]",
) + "</EndNote>"


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


def _endnote_instruction(xml_fragment: str, double_encode: bool = False) -> str:
    """Build a `` ADDIN EN.CITE <fragment> `` instruction.

    When ``double_encode`` is set, the fragment is HTML-entity-escaped
    BEFORE this string is embedded (and therefore XML-escaped again) into
    document.xml -- simulating EndNote's occasional extra encoding layer, so
    that once lxml unescapes document.xml's own entities exactly once, the
    resulting instrText text still contains literal ``&lt;EndNote&gt;...``
    rather than raw ``<EndNote>...``.
    """
    frag = xml_fragment
    if double_encode:
        frag = frag.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return " ADDIN EN.CITE " + frag + " "


def _split_three(text: str) -> list[str]:
    third = max(1, len(text) // 3)
    return [text[:third], text[third : 2 * third], text[2 * third :]]


def _nested_field(inner_instruction: str, inner_result: str, outer_result: str) -> str:
    """Outer PAGEREF field with an inner EndNote citation field nested inside."""
    runs = [_fldchar("begin"), _instr_run(r" PAGEREF _Ref0002 \h ")]
    runs.append(_field([inner_instruction], inner_result))
    runs.append(_fldchar("separate"))
    runs.append(_text_run(outer_result))
    runs.append(_fldchar("end"))
    return "".join(runs)


def _paragraph(*inner: str) -> str:
    return "<w:p>" + "".join(inner) + "</w:p>"


def build_document_xml() -> str:
    article_field = _field(_split_three(_endnote_instruction(SINGLE_XML)), "[1]")
    multi_field = _field([_endnote_instruction(MULTI_XML)], "[2, 3]")
    nested = _nested_field(_endnote_instruction(CHAPTER_XML), "[4]", "Section 3")
    double_instr = _endnote_instruction(DOUBLE_ENCODED_SOURCE_XML, double_encode=True)
    double_field = _field([double_instr], "[5]")

    body = "".join(
        [
            _paragraph(
                _text_run("Path integral formulations were introduced "),
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
                _text_run(", topological phases matter."),
            ),
            _paragraph(
                _text_run("Recent qubit coherence work "),
                double_field,
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
