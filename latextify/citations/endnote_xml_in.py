"""Parse a user-supplied EndNote XML library export into :class:`RefEntry` records.

EndNote's "Export... > XML" produces a flat
``<xml><records><record>...</record>...</records></xml>`` document. Each
``<record>`` uses EXACTLY the schema EndNote's Word plugin embeds per
citation (:mod:`latextify.citations.endnote`'s module docstring covers the
``<style>``-wrapped leaf values in detail), so this module is a thin
wrapper around that module's shared field extraction
(:func:`latextify.citations.endnote.record_to_refentry`) -- it just walks
every top-level ``<record>`` instead of every ``<Cite>``, and seeds each
entry's key from its own ``<rec-number>`` (the same "use the source's own
identifier" convention :mod:`latextify.citations.bibtex_in` follows for a
``.bib`` entry's citekey).

Parsed defensively: an uploaded EndNote export is untrusted input, so the
parser this module builds disables DTD loading, entity resolution, and
network access before any attacker-controlled bytes reach it -- an XXE
payload fails with a clean, caught ``XMLSyntaxError`` (surfaced as
``ValueError``) instead of resolving.
"""

from __future__ import annotations

from dataclasses import replace

from lxml import etree

from ..model.refs import RefEntry
from .endnote import record_to_refentry

# resolve_entities=False + load_dtd=False: an internal/external entity
# reference (the classic XXE payload) is left unresolved and fails to parse
# as an undefined entity, rather than being substituted. no_network=True
# additionally blocks any DTD/entity fetch over the network as a second
# layer, should either of the above ever be loosened by mistake.
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)


def parse_endnote_xml(data: bytes) -> list[RefEntry]:
    """Parse an EndNote XML export into a list of :class:`RefEntry` (order preserved).

    ``data`` must be bytes (not a decoded ``str``) so the document's own
    ``<?xml ... encoding="..."?>`` declaration, if any, is honored.

    Raises:
        ValueError: ``data`` is not well-formed XML, or has no
            ``<record>`` elements at all (not an EndNote library export).
    """
    try:
        root = etree.fromstring(data, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"not valid XML ({exc})") from exc

    records = root.findall(".//record")
    if not records:
        raise ValueError("not a valid EndNote XML export (no <record> elements found)")

    entries: list[RefEntry] = []
    for record in records:
        entry = record_to_refentry(record, source="endnote-xml")
        if entry is None:
            continue
        if entry.raw_id:
            entry = replace(entry, key=entry.raw_id)
        entries.append(entry)
    return entries
