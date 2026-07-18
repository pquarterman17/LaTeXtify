"""Parse a user-supplied CSL-JSON references export into :class:`RefEntry` records.

CSL-JSON (Zotero's "CSL JSON" export, and the format most other reference
managers offer alongside RIS/BibTeX) is either a bare JSON array of CSL
items, or -- some citeproc-js-based tools wrap it this way -- an object with
an ``"items"`` array. Either shape is accepted.

Each item is converted with the exact same CSL -> ``RefEntry`` mapping the
Zotero/Mendeley Word field-code path already uses
(:func:`latextify.citations.zotero.csl_item_to_refentry`), so title, author
family/given, ``issued.date-parts`` year, container-title, volume, issue,
page, and DOI are extracted identically regardless of whether the CSL item
came from a field code or a standalone export file. The item's own ``"id"``
becomes both the entry's key and its ``raw_id`` -- the same "use the
source's own identifier" convention :mod:`latextify.citations.bibtex_in`
follows for a ``.bib`` entry's citekey.
"""

from __future__ import annotations

import json
from dataclasses import replace

from ..model.refs import RefEntry
from .zotero import csl_item_to_refentry

_NOT_CSL_JSON = (
    "not valid CSL-JSON (expected a JSON array of items, or an object with "
    'an "items" array)'
)


def parse_csl_json(text: str) -> list[RefEntry]:
    """Parse CSL-JSON ``text`` into a list of :class:`RefEntry` (order preserved).

    Raises:
        ValueError: ``text`` is not valid JSON, or does not decode to an
            array of items (or an object with an ``"items"`` array).
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
    else:
        raise ValueError(_NOT_CSL_JSON)

    entries: list[RefEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = csl_item_to_refentry(item, source="csl-json")
        if entry.raw_id:
            entry = replace(entry, key=entry.raw_id)
        entries.append(entry)
    return entries
