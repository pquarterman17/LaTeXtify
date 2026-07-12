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
_LEADING_NUMBER_RE = re.compile(r"^\s*\[\d+\]\s*")
# A single-letter initial, optionally dotted: "L", "L.", the name particle "v."
# in "A. v. Chumak". These are skipped when hunting for the first real surname.
_INITIAL_RE = re.compile(r"^[A-Za-zÀ-ɏ]\.?$")
# A real (2+ char) name word, anchored so a token starting with a digit ("40th")
# is rejected rather than yielding a garbage surname.
_NAME_WORD_RE = re.compile(r"^[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'`-]+")


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


def score_fields(
    reference_text: str, *, title: str | None, year: str | None, surname: str | None
) -> float:
    """Blend title similarity, year match, and first-author surname match (0..1).

    The shared scoring core: title similarity uses rapidfuzz ``token_set_ratio``
    between the candidate title and the whole typed reference (the title is a
    substring of the typed text, so a set ratio rewards that overlap regardless
    of the surrounding author/journal tokens); the year and first-author checks
    corroborate. Used both for a Crossref candidate (:func:`score_candidate`)
    and for a local ``.bib`` entry (:mod:`latextify.citations.bibmatch`), so the
    two matching paths agree on what "confident" means.
    """
    title_sim = fuzz.token_set_ratio(title, reference_text) / 100.0 if title else 0.0
    year_ok = 1.0 if _year_in_text(year, reference_text) else 0.0
    author_ok = 1.0 if _surname_in_text(surname, reference_text) else 0.0
    return _W_TITLE * title_sim + _W_YEAR * year_ok + _W_AUTHOR * author_ok


def score_candidate(reference_text: str, candidate: CrossrefCandidate) -> float:
    """Blend a Crossref candidate's title/year/first-author against typed text."""
    return score_fields(
        reference_text,
        title=candidate.title,
        year=candidate.year,
        surname=candidate.first_author_surname,
    )


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
    """Best-effort first-author surname from a raw reference string.

    Skips leading single-letter initials so an initials-first name yields the
    real surname ("L. J. Cornelissen" -> "Cornelissen", not the initial "L").
    Also handles the surname-first form ("Foster, G." -> "Foster") since the
    first token there is already a real word. Getting this right matters beyond
    provenance: the surname seeds the BibTeX cite-key, and apsrev4-2 derives an
    author-less entry's disambiguation label from the key's first characters --
    so when every "B." author collapsed to key ``b20xx`` the labels collided and
    the bibliography rendered a stray "()" disambiguation marker. Real surnames
    give distinct keys and clean output. Only the leading names are scanned.
    """
    text = _LEADING_NUMBER_RE.sub("", text)
    for token in text.split()[:6]:
        core = token.strip(",;:")
        if not core or _INITIAL_RE.match(core):
            continue
        match = _NAME_WORD_RE.match(core)
        if match:
            return match.group(0)
    return None


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
    bib_entries: list[RefEntry] | None = None,
) -> ReconcileOutcome:
    """Reconcile every typed reference, returning keyed entries.

    When ``bib_entries`` is given (the author's own ``.bib`` export), each
    reference is matched against it FIRST -- authoritative, offline, no network
    round-trip -- and only references the ``.bib`` does not cover fall through to
    Crossref (:mod:`latextify.citations.bibmatch`). Crossref requests are issued
    serially (fine for one manuscript's reference list). Keys are assigned across
    the full list at the end so a/b/c collision suffixes are consistent with the
    field-code path.
    """
    # Lazy import breaks the reconcile <-> bibmatch cycle (bibmatch reuses this
    # module's score_fields); only paid when a .bib is actually supplied.
    from .bibmatch import best_bib_entry

    entries: list[RefEntry] = []
    # Provisional per-reference facts; keys are filled in after bulk assignment.
    pending: list[dict] = []

    for item in references:
        if bib_entries:
            bib_entry, bib_score = best_bib_entry(item.text, bib_entries)
            if bib_entry is not None and bib_score >= threshold:
                entries.append(bib_entry)
                pending.append(
                    {
                        "raw_text": item.text,
                        "source": "bibfile",
                        "matched": True,
                        "score": bib_score,
                        "doi": bib_entry.doi,
                        "verify": False,
                        "ref_number": item.number,
                        "matched_title": bib_entry.title,
                    }
                )
                continue

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
