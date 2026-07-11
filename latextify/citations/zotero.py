"""Zotero field code -> RefEntry.

Zotero's Word plugin stores each citation as a complex field whose assembled
instruction is::

    ADDIN ZOTERO_ITEM CSL_CITATION {<csl-json>}

The JSON's ``citationItems[].itemData`` is a full CSL item. This module owns
the shared CSL-item -> ``RefEntry`` conversion (also reused by ``mendeley``,
whose payload is the same CSL shape) plus the JSON extraction from a field
instruction.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..model.refs import Name, RefEntry
from .bib import csl_type_to_bibtex

MARKER = "ADDIN ZOTERO_ITEM CSL_CITATION"


def matches(instruction: str) -> bool:
    """True when an assembled field instruction is a Zotero citation."""
    return "ZOTERO_ITEM CSL_CITATION" in instruction


def extract_json(instruction: str) -> dict[str, Any]:
    """Parse the CSL JSON object embedded in a field instruction.

    Scans from the first ``{`` and decodes one JSON value, so trailing text
    after the object (rare) is ignored. Returns ``{}`` on any failure.
    """
    start = instruction.find("{")
    if start < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(instruction[start:])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_authors(raw: Any) -> tuple[Name, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[Name] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family", "")).strip()
        given = str(item.get("given", "")).strip()
        literal = str(item.get("literal", "")).strip()
        if not (family or given or literal):
            continue
        names.append(Name(family=family, given=given, literal=literal))
    return tuple(names)


def _parse_year(issued: Any) -> str | None:
    if not isinstance(issued, dict):
        return None
    date_parts = issued.get("date-parts")
    if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
        if date_parts[0]:
            match = re.search(r"\d{4}", str(date_parts[0][0]))
            if match:
                return match.group(0)
    for key in ("raw", "literal"):
        value = issued.get(key)
        if value:
            match = re.search(r"\d{4}", str(value))
            if match:
                return match.group(0)
    return None


def csl_item_to_refentry(item: dict[str, Any], source: str) -> RefEntry:
    """Convert one CSL ``itemData`` dict to a keyless ``RefEntry``."""
    csl_type = str(item.get("type", "")).strip()
    return RefEntry(
        key="",
        entry_type=csl_type_to_bibtex(csl_type),
        csl_type=csl_type,
        title=_clean(item.get("title")),
        authors=_parse_authors(item.get("author")),
        year=_parse_year(item.get("issued")),
        container_title=_clean(item.get("container-title")),
        publisher=_clean(item.get("publisher")),
        volume=_clean(item.get("volume")),
        issue=_clean(item.get("issue")),
        pages=_clean(item.get("page")),
        doi=_clean(item.get("DOI") or item.get("doi")),
        url=_clean(item.get("URL") or item.get("url")),
        isbn=_clean(item.get("ISBN") or item.get("isbn")),
        source=source,
        raw_id=_clean(item.get("id")),
    )


def refentries_from_payload(payload: dict[str, Any], source: str) -> list[RefEntry]:
    """Turn a decoded CSL_CITATION payload into one RefEntry per citation item."""
    entries: list[RefEntry] = []
    items = payload.get("citationItems") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return entries
    for citation_item in items:
        if not isinstance(citation_item, dict):
            continue
        item_data = citation_item.get("itemData")
        if isinstance(item_data, dict):
            entries.append(csl_item_to_refentry(item_data, source))
    return entries


def parse_instruction(instruction: str, source: str = "zotero") -> list[RefEntry]:
    """Parse a Zotero field instruction into RefEntry objects (one per item)."""
    return refentries_from_payload(extract_json(instruction), source)
