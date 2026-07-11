"""Confidence-scored reconciliation of typed references (plan item 14).

Takes the reference strings segmented from a manuscript's typed bibliography and,
for each one, picks the best Crossref candidate: a weighted blend of rapidfuzz
title similarity, a year match, and a first-author surname match. A candidate at
or above :data:`DEFAULT_THRESHOLD` is accepted (a ``crossref`` reference with its
DOI); otherwise the reference is emitted verbatim from its typed text and flagged
``verify`` so the report (plan item 16) can surface it loudly.

Every reference yields both a :class:`~latextify.model.refs.RefEntry` (for the
``.bib``) and a :class:`~latextify.model.reconcile.ReconcileRecord` (for the
report). BibTeX keys are assigned in one pass over the whole list -- via the
shared :func:`latextify.citations.bib.assign_keys` -- so collisions get a/b/c
suffixes exactly as the field-code path does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..model.reconcile import ReconcileRecord
from ..model.refs import Name, RefEntry
from .bib import assign_keys
from .crossref import CrossrefCandidate, CrossrefClient

#: Accept a Crossref candidate whose blended score reaches this. Chosen so a good
#: match (title similarity high, year present, first author present in the typed
#: text) clears it comfortably while a wrong or missing candidate does not.
DEFAULT_THRESHOLD = 0.72

# Score weights: title similarity dominates; year + first-author are corroborating.
_W_TITLE = 0.6
_W_YEAR = 0.2
_W_AUTHOR = 0.2

_YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
_LEADING_SURNAME_RE = re.compile(r"^\s*(?:\[\d+\]\s*)?([A-Z][A-Za-zÀ-ɏ'`-]+)")


@dataclass(frozen=True)
class ReferenceItem:
    """One entry segmented from a typed reference list."""

    text: str
    number: int | None = None


@dataclass
class ReconcileOutcome:
    """Result of reconciling a whole reference list."""

    entries: list[RefEntry]
    records: tuple[ReconcileRecord, ...]


def _year_in_text(year: str | None, text: str) -> bool:
    return bool(year) and year in text


def _surname_in_text(surname: str | None, text: str) -> bool:
    if not surname:
        return False
    return re.search(rf"\b{re.escape(surname)}\b", text, re.IGNORECASE) is not None


def score_candidate(reference_text: str, candidate: CrossrefCandidate) -> float:
    """Blend title similarity, year match, and first-author surname match (0..1).

    Title similarity uses rapidfuzz ``token_set_ratio`` between the candidate
    title and the whole typed reference (the title is a substring of the typed
    text, so a set ratio rewards that overlap regardless of the surrounding
    author/journal tokens). The year and first-author checks corroborate.
    """
    if candidate.title:
        title_sim = fuzz.token_set_ratio(candidate.title, reference_text) / 100.0
    else:
        title_sim = 0.0
    year_ok = 1.0 if _year_in_text(candidate.year, reference_text) else 0.0
    author_ok = 1.0 if _surname_in_text(candidate.first_author_surname, reference_text) else 0.0
    return _W_TITLE * title_sim + _W_YEAR * year_ok + _W_AUTHOR * author_ok


def best_candidate(
    reference_text: str, candidates: list[CrossrefCandidate]
) -> tuple[CrossrefCandidate | None, float]:
    """Return the highest-scoring candidate and its score (``None``, 0.0 if empty)."""
    best: CrossrefCandidate | None = None
    best_score = 0.0
    for candidate in candidates:
        score = score_candidate(reference_text, candidate)
        if score > best_score or best is None:
            best, best_score = candidate, score
    return best, best_score


def _guess_surname(text: str) -> str | None:
    match = _LEADING_SURNAME_RE.match(text)
    return match.group(1) if match else None


def _guess_year(text: str) -> str | None:
    match = _YEAR_RE.search(text)
    return match.group(0) if match else None


def raw_refentry(reference_text: str) -> RefEntry:
    """Build a keyless fallback entry from typed text when no match is confident.

    The whole typed reference becomes the ``title`` so nothing is lost; a
    best-effort surname + year feed the citation key. ``entry_type`` is ``misc``.
    """
    surname = _guess_surname(reference_text)
    year = _guess_year(reference_text)
    authors: tuple[Name, ...] = (Name(family=surname),) if surname else ()
    return RefEntry(
        key="",
        entry_type="misc",
        title=reference_text,
        authors=authors,
        year=year,
        source="raw",
    )


def reconcile_references(
    references: list[ReferenceItem],
    client: CrossrefClient,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    rows: int = 3,
) -> ReconcileOutcome:
    """Reconcile every typed reference against Crossref, returning keyed entries.

    Requests are issued serially (fine for one manuscript's reference list). Keys
    are assigned across the full list at the end so a/b/c collision suffixes are
    consistent with the field-code path.
    """
    entries: list[RefEntry] = []
    # Provisional per-reference facts; keys are filled in after bulk assignment.
    pending: list[dict] = []

    for item in references:
        candidates = client.query_bibliographic(item.text, rows=rows)
        candidate, score = best_candidate(item.text, candidates)
        if candidate is not None and score >= threshold:
            entries.append(candidate.to_refentry())
            pending.append(
                {
                    "raw_text": item.text,
                    "source": "crossref",
                    "matched": True,
                    "score": score,
                    "doi": candidate.doi,
                    "verify": False,
                    "ref_number": item.number,
                    "matched_title": candidate.title,
                }
            )
        else:
            entries.append(raw_refentry(item.text))
            pending.append(
                {
                    "raw_text": item.text,
                    "source": "raw",
                    "matched": False,
                    "score": score,
                    "doi": None,
                    "verify": True,
                    "ref_number": item.number,
                    "matched_title": None,
                }
            )

    keyed = assign_keys(entries)
    records = tuple(
        ReconcileRecord(key=entry.key, **facts) for entry, facts in zip(keyed, pending, strict=True)
    )
    return ReconcileOutcome(entries=keyed, records=records)
