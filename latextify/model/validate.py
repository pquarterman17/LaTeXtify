"""Online reference-validation records (report on `--check-references`).

When a network connection is available and validation is requested, every
reference in the assembled ``.bib`` is checked against Crossref's authoritative
record: a reference that carries a DOI is looked up exactly
(``/works/{doi}``); one without a DOI is searched for so a DOI can be
suggested. The stored bibliographic fields (title, authors, year, journal,
volume, issue, pages) are then compared field-by-field.

These frozen records feed the report (:mod:`latextify.report.render`); the
validation itself lives in :mod:`latextify.citations.validate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: A reference's validation outcome.
#:   verified      -- had a DOI, it resolved, every compared field matched
#:   mismatch      -- had a DOI, it resolved, but a compared field differs
#:   dead_doi      -- had a DOI, but Crossref has no such work (typo / bad DOI)
#:   doi_suggested -- no DOI; a confident Crossref match was found (DOI to add)
#:   unverifiable  -- no DOI and no confident match; can't be checked
#:   unchecked     -- network unavailable when this reference was reached
STATUS_VALUES = (
    "verified",
    "mismatch",
    "dead_doi",
    "doi_suggested",
    "unverifiable",
    "unchecked",
)

#: Statuses that warrant the author's attention in the report.
FLAGGED_STATUSES = frozenset({"mismatch", "dead_doi", "doi_suggested", "unverifiable"})


@dataclass(frozen=True)
class FieldCheck:
    """One field compared between our reference and the canonical record."""

    field: str  # "title" | "authors" | "year" | "journal" | "volume" | "issue" | "pages"
    ours: str
    canonical: str
    ok: bool


@dataclass(frozen=True)
class ValidationRecord:
    """The validation outcome for a single reference."""

    key: str
    status: str
    doi: str | None = None
    suggested_doi: str | None = None
    checks: tuple[FieldCheck, ...] = ()
    note: str = ""

    @property
    def flagged(self) -> bool:
        return self.status in FLAGGED_STATUSES

    @property
    def problems(self) -> tuple[FieldCheck, ...]:
        """The compared fields that did not match."""
        return tuple(c for c in self.checks if not c.ok)


@dataclass(frozen=True)
class ValidationReport:
    """All reference-validation records for one project."""

    records: tuple[ValidationRecord, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.records)

    def count(self, status: str) -> int:
        return sum(1 for r in self.records if r.status == status)

    @property
    def flagged_count(self) -> int:
        return sum(1 for r in self.records if r.flagged)

    @property
    def any_checked(self) -> bool:
        """True if at least one reference was actually reached (not all offline)."""
        return any(r.status != "unchecked" for r in self.records)
