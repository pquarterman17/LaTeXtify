"""Reconciliation IR for plain-text citation reconstruction (plan item 14).

Frozen dataclasses only -- no behavior, no I/O (per the ``model/`` contract).
When a manuscript has no citation field codes, :mod:`latextify.citations.plaintext`
reconstructs its bibliography from the typed reference list by querying Crossref
and scoring candidates. Every reference -- whether confidently matched or flagged
for human review -- produces one :class:`ReconcileRecord`. These records are
designed to be consumed as-is by the consolidated conversion report (plan item
16): each carries the source, the confidence score, the resolved DOI (or
``None``), and a ``verify`` flag that item 16 renders loudly for low-confidence
matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReconcileRecord:
    """One typed reference's reconciliation outcome.

    ``raw_text`` is the reference exactly as segmented from the document (minus
    a stripped list number). ``key`` is the BibTeX key finally assigned to the
    entry. ``source`` is ``"crossref"`` for an accepted Crossref match or
    ``"raw"`` for a reference emitted from its typed text because no candidate
    cleared the confidence threshold. ``matched`` mirrors that distinction as a
    bool; ``score`` is the best candidate's 0..1 confidence (0.0 when Crossref
    returned nothing). ``doi`` is the matched DOI or ``None``. ``verify`` is
    ``True`` whenever a human should double-check the entry (every below-threshold
    reference), which is exactly the set item 16's report must surface loudly.
    ``ref_number`` is the reference's 1-based list position when the list was
    numbered (used to pair numeric in-text markers), else ``None``.
    ``matched_title`` is the accepted candidate's title, kept for report
    provenance.
    """

    raw_text: str
    key: str
    source: str
    matched: bool
    score: float
    doi: str | None = None
    verify: bool = False
    ref_number: int | None = None
    matched_title: str | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    """Every reconciliation record from one reconstruction run, in list order.

    The aggregate item 16's report consumes. Convenience counts are computed
    from ``records`` so the report renderer does not have to recount.
    """

    records: tuple[ReconcileRecord, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def matched_count(self) -> int:
        return sum(1 for r in self.records if r.matched)

    @property
    def flagged_count(self) -> int:
        return sum(1 for r in self.records if r.verify)

    @property
    def matched_fraction(self) -> float:
        """Share of references confidently matched (0.0 when there are none)."""
        return self.matched_count / self.total if self.records else 0.0
