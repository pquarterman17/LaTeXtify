"""Author-year lookup index for the plain-text citation reconstruction path.

Split out of :mod:`latextify.citations.plaintext` (which sits at its own
line-count ratchet pin, ``tests/test_repo_integrity.py``) since this is a
self-contained unit: build a ``(surname, year) -> [key, ...]`` index over a
reconstructed reference list, for :func:`~latextify.citations.plaintext.link_body_markers`
to resolve ``(Smith et al., 2020)``-style in-text markers against.
"""

from __future__ import annotations

import re

from ..model.refs import RefEntry

# Leading initials of a Western author name at a raw citation's start, e.g.
# "J. E. " in "J. E. Davies, O. Hellwig, ...", so the surname after them can be
# picked out.
_RAW_INITIALS_RE = re.compile(r"^(?:[A-Z]\.[\s]*)+")


def _raw_leading_surname(title: str) -> str | None:
    """Leading author surname parsed from a raw-text reference's title.

    A Crossref-unmatched entry keeps the whole typed citation in its ``.title``
    ("J. E. Davies, O. Hellwig, ... (2004).") with no structured author, so the
    author-year index would otherwise never point at it. Pull the first author's
    surname ("davies") after any leading initials. Returns ``None`` when the head
    (text before the first comma) does not look like an author name -- e.g. a
    "See Supplemental Material ..." note -- so junk is not indexed.
    """
    head = title.split(",", 1)[0].strip()
    head = _RAW_INITIALS_RE.sub("", head).strip()
    if not head:
        return None
    first = head.split()[0].strip(".'`-").lower()
    return first if first.isalpha() and len(first) >= 2 else None


def build_author_year_index(entries: list[RefEntry]) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        if not entry.year:
            continue
        if entry.authors:
            first = entry.authors[0]
            surname = (first.family or first.literal).strip().lower()
        else:
            # Raw-text (Crossref-unmatched) entry: surname lives in the title.
            surname = _raw_leading_surname(entry.title or "") or ""
        if not surname:
            continue
        index.setdefault((surname, entry.year), []).append(entry.key)
    return index
