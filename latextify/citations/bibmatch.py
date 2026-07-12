"""Offline citation matching against a user-supplied ``.bib``.

When a manuscript carries no citation field codes, the plain-text
reconstruction path (:mod:`latextify.citations.plaintext`) normally reconciles
each typed reference against Crossref. If the author also hands us their
reference manager's ``.bib`` export (parsed by
:mod:`latextify.citations.bibtex_in`), we can match locally FIRST --
authoritative, offline, no network round-trip -- and only fall back to Crossref
for references the ``.bib`` doesn't cover.

The scoring reuses the exact title/year/author blend Crossref matching uses
(:func:`latextify.citations.reconcile.score_fields`) so "confident" means the
same thing on both paths; only the candidate source differs (a local
:class:`~latextify.model.refs.RefEntry` here vs a ``CrossrefCandidate`` there).
"""

from __future__ import annotations

from ..model.refs import RefEntry
from .reconcile import score_fields


def first_author_surname(entry: RefEntry) -> str | None:
    """The first author's surname for corroboration scoring (``None`` if absent).

    Prefers the structured ``family`` name; falls back to a ``literal`` name
    (corporate author) so "CERN Collaboration"-style entries still contribute a
    surname signal.
    """
    if not entry.authors:
        return None
    first = entry.authors[0]
    surname = (first.family or first.literal or "").strip()
    return surname or None


def score_bib_entry(reference_text: str, entry: RefEntry) -> float:
    """Confidence (0..1) that ``entry`` is the reference typed as ``reference_text``."""
    return score_fields(
        reference_text,
        title=entry.title,
        year=entry.year,
        surname=first_author_surname(entry),
    )


def best_bib_entry(
    reference_text: str, entries: list[RefEntry]
) -> tuple[RefEntry | None, float]:
    """Return the highest-scoring ``.bib`` entry and its score (``None``, 0.0 if empty)."""
    best: RefEntry | None = None
    best_score = 0.0
    for entry in entries:
        score = score_bib_entry(reference_text, entry)
        if score > best_score or best is None:
            best, best_score = entry, score
    return best, best_score
