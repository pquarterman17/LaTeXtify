"""Online reference validation against Crossref's authoritative record.

Given the assembled bibliography, every reference is checked against Crossref
when a network connection is available (opt-in: CLI ``--check-references`` /
GUI checkbox / ``emit_project(check_references=True)``):

* a reference that **carries a DOI** is resolved exactly (``/works/{doi}``).
  The DOI either resolves to a work whose fields we compare
  (:data:`~latextify.model.validate.STATUS_VALUES` ``verified`` / ``mismatch``)
  or it does not exist in Crossref (``dead_doi`` -- a typo or fabricated DOI);
* a reference **without a DOI** is searched for bibliographically; a confident
  match yields a DOI to add (``doi_suggested``), otherwise it is ``unverifiable``.

Field comparison is deliberately lenient where formatting legitimately varies
(journal abbreviations, page ranges cited as a first page) and strict where a
difference signals a real error (year, volume). See the module-level threshold
constants for the exact tolerances.

This module owns the *logic*; the frozen result records live in
:mod:`latextify.model.validate` and the human-readable rendering in the report.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from ..model.refs import Name, RefEntry
from ..model.validate import FieldCheck, ValidationRecord, ValidationReport
from .crossref import CrossrefCandidate, CrossrefClient, CrossrefUnavailable
from .reconcile import DEFAULT_THRESHOLD, best_candidate

#: A title matches if rapidfuzz ``token_sort_ratio`` (order-insensitive, full
#: comparison) reaches this. High enough to catch a genuinely different paper,
#: forgiving of trailing-punctuation / subtitle-separator noise.
TITLE_MIN = 90
#: Each author surname compared position-by-position must reach this ``ratio``
#: (a one-character typo in a surname stays above it; a different name does not).
AUTHOR_MIN = 85
#: Our journal string must match Crossref's full OR abbreviated container title
#: at least this well -- low, because "Phys. Rev. B" vs "Physical Review B"
#: shares few characters; the goal is only to catch a *wrong* journal.
JOURNAL_MIN = 55

# Unicode dash variants (figure/en/em/minus/hyphen) normalized to "-" so a page
# range typed with an en dash compares equal to Crossref's hyphenated one.
_DASH_RE = re.compile(r"[‐-―−]")
_WS_RE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    """Fold accented Latin letters to ASCII ("Müller" -> "Muller", "Néel" -> "Neel").

    Author names in physics manuscripts routinely differ from Crossref only by
    accent transliteration -- "González"/"Gonzalez", "Néel"/"Neel". Folding both
    sides before the fuzzy comparison keeps those legitimate variants scoring as
    identical instead of landing in the same similarity band as a real typo.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _norm(text: str | None) -> str:
    """Accent-fold, lowercase, collapse whitespace -- baseline for fuzzy matching."""
    if not text:
        return ""
    return _WS_RE.sub(" ", _strip_accents(text)).strip().lower()


def _surnames(names: tuple[Name, ...]) -> list[str]:
    out: list[str] = []
    for name in names:
        surname = (name.family or name.literal).strip()
        if surname:
            out.append(surname)
    return out


def _format_authors(names: tuple[Name, ...]) -> str:
    """Compact surname list for the report ("Smith, Jones, +3")."""
    surnames = _surnames(names)
    if not surnames:
        return ""
    if len(surnames) <= 3:
        return ", ".join(surnames)
    return ", ".join(surnames[:3]) + f", +{len(surnames) - 3}"


def _title_check(entry: RefEntry, candidate: CrossrefCandidate) -> FieldCheck | None:
    if not entry.title or not candidate.title:
        return None
    score = fuzz.token_sort_ratio(_norm(entry.title), _norm(candidate.title))
    return FieldCheck(
        field="title", ours=entry.title, canonical=candidate.title, ok=score >= TITLE_MIN
    )


def _authors_check(entry: RefEntry, candidate: CrossrefCandidate) -> FieldCheck | None:
    ours = _surnames(entry.authors)
    canon = _surnames(candidate.authors)
    if not ours or not canon:
        return None
    # Position-by-position surname comparison; a differing count is itself a
    # mismatch (a dropped or added author). Only the overlapping prefix is
    # fuzz-compared, so a single typo'd surname flags without every later
    # author cascading.
    ok = len(ours) == len(canon)
    if ok:
        for a, b in zip(ours, canon, strict=False):
            if fuzz.ratio(_norm(a), _norm(b)) < AUTHOR_MIN:
                ok = False
                break
    return FieldCheck(
        field="authors",
        ours=_format_authors(entry.authors),
        canonical=_format_authors(candidate.authors),
        ok=ok,
    )


def _year_check(entry: RefEntry, candidate: CrossrefCandidate) -> FieldCheck | None:
    if not entry.year or not candidate.year:
        return None
    return FieldCheck(
        field="year",
        ours=entry.year,
        canonical=candidate.year,
        ok=entry.year.strip() == candidate.year.strip(),
    )


def _journal_check(entry: RefEntry, candidate: CrossrefCandidate) -> FieldCheck | None:
    ours = entry.container_title
    if not ours:
        return None
    canon_full = candidate.container_title
    canon_short = candidate.short_container_title
    if not canon_full and not canon_short:
        return None
    # Accept a match against EITHER the full or the abbreviated journal name, so
    # "Phys. Rev. B" (abbreviated in the manuscript) validates against Crossref's
    # short-container-title without a false "wrong journal" flag.
    best = 0.0
    for canon in (canon_full, canon_short):
        if canon:
            best = max(best, fuzz.token_set_ratio(_norm(ours), _norm(canon)))
    return FieldCheck(
        field="journal",
        ours=ours,
        canonical=canon_full or canon_short or "",
        ok=best >= JOURNAL_MIN,
    )


def _exact_check(field: str, ours: str | None, canon: str | None) -> FieldCheck | None:
    if not ours or not canon:
        return None
    return FieldCheck(field=field, ours=ours, canonical=canon, ok=ours.strip() == canon.strip())


def _norm_pages(pages: str | None) -> str | None:
    if not pages:
        return None
    return _DASH_RE.sub("-", pages).replace(" ", "")


def _pages_check(entry: RefEntry, candidate: CrossrefCandidate) -> FieldCheck | None:
    ours = _norm_pages(entry.pages)
    canon = _norm_pages(candidate.pages)
    if not ours or not canon:
        return None
    # Equal after dash/space normalization, OR the same first page: many house
    # styles cite only the article's first page ("1234") where Crossref records
    # a full range ("1234-1241").
    ok = ours == canon or ours.split("-")[0] == canon.split("-")[0]
    return FieldCheck(field="pages", ours=entry.pages or "", canonical=candidate.pages or "", ok=ok)


def compare_entry_to_candidate(
    entry: RefEntry, candidate: CrossrefCandidate
) -> tuple[FieldCheck, ...]:
    """Compare every field present on *both* sides; skip fields either side lacks.

    A field only produces a :class:`FieldCheck` when both our entry and the
    canonical record carry it -- a value we simply do not store is an omission,
    not an error, and comparing against a blank would flag every incomplete
    reference. The returned order is stable (title, authors, year, journal,
    volume, issue, pages) so the report reads consistently.
    """
    checks = [
        _title_check(entry, candidate),
        _authors_check(entry, candidate),
        _year_check(entry, candidate),
        _journal_check(entry, candidate),
        _exact_check("volume", entry.volume, candidate.volume),
        _exact_check("issue", entry.issue, candidate.issue),
        _pages_check(entry, candidate),
    ]
    return tuple(c for c in checks if c is not None)


def _query_text(entry: RefEntry) -> str:
    """Synthesize a bibliographic query string from a structured entry.

    Feeds Crossref the same signal a typed reference would: authors, title,
    year, journal. The scorer (:func:`best_candidate`) then matches a returned
    candidate's title/year/first-author against this text.
    """
    parts = [
        ", ".join(_surnames(entry.authors)),
        entry.title or "",
        entry.year or "",
        entry.container_title or "",
    ]
    return " ".join(p for p in parts if p).strip()


def validate_entry(
    entry: RefEntry, client: CrossrefClient, *, threshold: float = DEFAULT_THRESHOLD
) -> ValidationRecord:
    """Validate one reference against Crossref.

    DOI path: resolve the DOI. Missing DOI -> ``dead_doi``; resolved + all
    fields match -> ``verified``; resolved + a field differs -> ``mismatch``.
    Crossref unreachable -> ``unchecked`` (never blamed on the reference).

    No-DOI path: bibliographic search; a candidate at/above ``threshold`` that
    itself carries a DOI -> ``doi_suggested`` (with the field comparison, so the
    author can sanity-check the suggestion); otherwise ``unverifiable``.
    """
    if entry.doi:
        try:
            canonical = client.get_by_doi(entry.doi)
        except CrossrefUnavailable:
            return ValidationRecord(key=entry.key, status="unchecked", doi=entry.doi)
        if canonical is None:
            return ValidationRecord(
                key=entry.key,
                status="dead_doi",
                doi=entry.doi,
                note="DOI does not resolve in Crossref",
            )
        checks = compare_entry_to_candidate(entry, canonical)
        status = "verified" if all(c.ok for c in checks) else "mismatch"
        return ValidationRecord(
            key=entry.key,
            status=status,
            doi=entry.doi,
            checks=checks,
            canonical_entry=canonical.to_refentry(),
        )

    query = _query_text(entry)
    try:
        candidates = client.query_bibliographic_checked(query)
    except CrossrefUnavailable:
        # An outage on the no-DOI path must trip the same offline short-circuit
        # the DOI path does; otherwise validate_references keeps issuing doomed
        # queries and mislabels every no-DOI reference "unverifiable".
        return ValidationRecord(key=entry.key, status="unchecked", doi=entry.doi)
    candidate, score = best_candidate(query, candidates)
    if candidate is not None and score >= threshold and candidate.doi:
        checks = compare_entry_to_candidate(entry, candidate)
        return ValidationRecord(
            key=entry.key,
            status="doi_suggested",
            suggested_doi=candidate.doi,
            checks=checks,
            note="no DOI in reference; Crossref match found",
            canonical_entry=candidate.to_refentry(),
        )
    return ValidationRecord(
        key=entry.key, status="unverifiable", note="no DOI and no confident Crossref match"
    )


def validate_references(
    entries: list[RefEntry], client: CrossrefClient, *, threshold: float = DEFAULT_THRESHOLD
) -> ValidationReport:
    """Validate a whole bibliography, one serial Crossref pass.

    Requests are issued serially -- fine for a single manuscript's reference
    list, and it keeps us in Crossref's polite pool. Once a request fails with
    :class:`CrossrefUnavailable` we treat the network as down for the rest of
    the run: every remaining reference is recorded ``unchecked`` without a
    request, so a mid-run outage produces "couldn't check" rather than a wall of
    spurious ``unverifiable`` flags (and dozens of doomed timeouts).
    """
    records: list[ValidationRecord] = []
    offline = False
    for entry in entries:
        if offline:
            records.append(
                ValidationRecord(key=entry.key, status="unchecked", doi=entry.doi)
            )
            continue
        record = validate_entry(entry, client, threshold=threshold)
        if record.status == "unchecked":
            offline = True
        records.append(record)
    return ValidationReport(records=tuple(records))
