"""EndNote field code -> RefEntry.

EndNote's Word plugin stores each citation as a complex field whose
assembled instruction is::

    ADDIN EN.CITE <EndNote><Cite><Author>...</Author><Year>...</Year>
    <RecNum>...</RecNum><record>...</record></Cite></EndNote>

A single field can carry MULTIPLE ``<Cite>`` elements (a multi-item
citation, e.g. ``[3, 4]``). Each ``<Cite>``'s ``<record>`` holds the full
reference data: ``titles/title``, ``contributors/authors/author`` (one per
author, "Family, Given" text), ``dates/year``, ``periodical/full-title`` or
``titles/secondary-title`` (journal), ``electronic-resource-num`` (DOI),
``pages``, ``volume``, and a ``ref-type name="..."`` attribute for the entry
type. Most leaf values are frequently wrapped in an EndNote ``<style>``
child (e.g. ``<year><style face="normal" ...>2020</style></year>``) rather
than holding text directly -- :func:`_leaf_text` unwraps both forms.

The embedded ``<EndNote>...</EndNote>`` XML fragment is usually plain text
once lxml has already unescaped ``document.xml``'s own entities, but some
EndNote versions add a SECOND layer of HTML-entity encoding on top (so the
assembled instruction contains literal ``&lt;EndNote&gt;...`` text rather
than ``<EndNote>...``). :func:`_extract_endnote_xml` tries both forms and
degrades to ``None`` -- never raises -- for anything that still doesn't
parse, so :func:`parse_instruction` returns an empty list instead of
crashing on malformed field data.
"""

from __future__ import annotations

import html
from typing import Any

from lxml import etree

from ..model.refs import Name, RefEntry
from .bib import csl_type_to_bibtex

MARKER = "ADDIN EN.CITE"

_REF_TYPE_TO_CSL = {
    "journal article": "article-journal",
    "electronic article": "article-journal",
    "magazine article": "article-magazine",
    "newspaper article": "article-newspaper",
    "book": "book",
    "book section": "chapter",
    "conference paper": "paper-conference",
    "conference proceedings": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "web page": "webpage",
    "manuscript": "manuscript",
    "patent": "patent",
}


def matches(instruction: str) -> bool:
    """True when an assembled field instruction is an EndNote citation."""
    return "ADDIN EN.CITE" in instruction


def _isolate_fragment(instruction: str) -> str | None:
    """Slice the ``<EndNote>...</EndNote>`` (or HTML-entity-encoded) span.

    Tries the raw form first, then the encoded form. Returns everything from
    the start marker onward (rather than ``None``) when the matching end
    marker is missing, so a truncated/malformed fragment still reaches the
    XML parser and fails there in a controlled way.
    """
    for start_marker, end_marker in (
        ("<EndNote", "</EndNote>"),
        ("&lt;EndNote", "&lt;/EndNote&gt;"),
    ):
        start = instruction.find(start_marker)
        if start < 0:
            continue
        end = instruction.find(end_marker, start)
        if end < 0:
            return instruction[start:]
        return instruction[start : end + len(end_marker)]
    return None


def _extract_endnote_xml(instruction: str) -> Any:
    """Parse the EndNote XML fragment out of a field instruction.

    Handles both a raw embedded fragment and one wrapped in an extra layer
    of HTML-entity encoding, trying an ``html.unescape`` pass on whichever
    candidate form doesn't parse outright. Returns ``None`` -- never raises
    -- when the fragment is missing or malformed either way.
    """
    fragment = _isolate_fragment(instruction)
    if fragment is None:
        return None
    for candidate in (fragment, html.unescape(fragment)):
        try:
            return etree.fromstring(candidate.encode("utf-8"))
        except etree.XMLSyntaxError:
            continue
    return None


def _leaf_text(elem) -> str | None:
    """Text of a record leaf element, unwrapping an EndNote ``<style>`` child."""
    if elem is None:
        return None
    style = elem.find("style")
    text = style.text if style is not None else elem.text
    text = (text or "").strip()
    return text or None


def _find_text(record, path: str) -> str | None:
    return _leaf_text(record.find(path))


def _parse_author_name(raw: str) -> Name:
    # Corporate/institutional authors are conventionally given a trailing
    # comma with no given-name part, e.g. "International Astronomical Union,".
    if raw.endswith(","):
        literal = raw.rstrip(",").strip()
        return Name(literal=literal) if literal else Name()
    if "," in raw:
        family, _, given = raw.partition(",")
        return Name(family=family.strip(), given=given.strip())
    return Name(literal=raw)


def _parse_authors(record) -> tuple[Name, ...]:
    names: list[Name] = []
    for author in record.findall("contributors/authors/author"):
        raw = _leaf_text(author)
        if raw:
            names.append(_parse_author_name(raw))
    return tuple(names)


def _journal(record) -> str | None:
    return (
        _find_text(record, "periodical/full-title")
        or _find_text(record, "periodical/abbr-1")
        or _find_text(record, "titles/secondary-title")
    )


def _ref_type(record) -> str:
    elem = record.find("ref-type")
    if elem is None:
        return ""
    name = (elem.get("name") or "").strip().lower()
    return _REF_TYPE_TO_CSL.get(name, "")


def cite_to_refentry(cite_elem, source: str = "endnote") -> RefEntry | None:
    """Convert one ``<Cite>`` element to a keyless ``RefEntry``.

    Returns ``None`` (never raises) when the ``<Cite>`` has no ``<record>``
    -- a malformed field that should be skipped, not crash the extraction.
    """
    record = cite_elem.find("record")
    if record is None:
        return None
    csl_type = _ref_type(record)
    return RefEntry(
        key="",
        entry_type=csl_type_to_bibtex(csl_type),
        csl_type=csl_type,
        title=_find_text(record, "titles/title"),
        authors=_parse_authors(record),
        year=_find_text(record, "dates/year"),
        container_title=_journal(record),
        volume=_find_text(record, "volume"),
        pages=_find_text(record, "pages"),
        doi=_find_text(record, "electronic-resource-num"),
        source=source,
        raw_id=_find_text(record, "rec-number"),
    )


def parse_instruction(instruction: str, source: str = "endnote") -> list[RefEntry]:
    """Parse an EndNote field instruction into RefEntry objects (one per ``<Cite>``).

    Degrades to an empty list -- never raises -- for a field whose embedded
    XML doesn't parse, or a ``<Cite>`` with no usable ``<record>``.
    """
    root = _extract_endnote_xml(instruction)
    if root is None:
        return []
    entries: list[RefEntry] = []
    for cite_elem in root.findall(".//Cite"):
        entry = cite_to_refentry(cite_elem, source)
        if entry is not None:
            entries.append(entry)
    return entries
