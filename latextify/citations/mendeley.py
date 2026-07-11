"""Mendeley field code -> RefEntry.

Mendeley's Word plugin stores citations as complex fields whose assembled
instruction is::

    ADDIN CSL_CITATION {<csl-json>}

The payload is the same CSL shape as Zotero's (minus the ``ZOTERO_ITEM``
token), so this module reuses the shared CSL conversion in :mod:`zotero`.
"""

from __future__ import annotations

from ..model.refs import RefEntry
from . import zotero

MARKER = "ADDIN CSL_CITATION"


def matches(instruction: str) -> bool:
    """True for a Mendeley citation field (CSL_CITATION without ZOTERO_ITEM)."""
    return "ADDIN CSL_CITATION" in instruction and "ZOTERO_ITEM" not in instruction


def parse_instruction(instruction: str, source: str = "mendeley") -> list[RefEntry]:
    """Parse a Mendeley field instruction into RefEntry objects."""
    return zotero.refentries_from_payload(zotero.extract_json(instruction), source)
