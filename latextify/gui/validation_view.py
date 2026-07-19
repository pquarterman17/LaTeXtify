"""Shape a reference-validation report into the review panel's JSON.

Pure data-shaping (no route logic, no I/O): a :class:`~latextify.model.
validate.ValidationReport` plus the entry set it was built from becomes the
flat :class:`~latextify.gui.schemas.ValidationOut` shape the review-panel JS
(``static/review.js``) renders. Extracted out of :mod:`latextify.gui.server`
-- which only calls it -- to keep that module under its size-ratchet pin
(see ``tests/test_repo_integrity.py``); a pure helper with no FastAPI
dependency belongs in its own focused module rather than growing the route
file.
"""

from __future__ import annotations

from latextify.citations.corrections import entry_to_dict
from latextify.gui.schemas import FieldProblemOut, ValidationOut, ValidationRecordOut
from latextify.model.refs import RefEntry
from latextify.model.validate import ValidationReport

_VALIDATION_STATUS_ORDER = (
    "verified", "mismatch", "dead_doi", "doi_suggested", "unverifiable", "unchecked",
)


def build_validation_out(
    report: ValidationReport, entries: tuple[RefEntry, ...]
) -> ValidationOut:
    """Shape a ValidationReport + entries into the review panel's JSON.

    Only flagged references become records (the panel reviews those); each
    carries the current entry and Crossref's version as flat editable fields so
    the UI can render approve/deny and prefill the whole-entry editor.
    """
    entries_by_key = {e.key: e for e in entries}
    counts = {s: report.count(s) for s in _VALIDATION_STATUS_ORDER if report.count(s)}
    records: list[ValidationRecordOut] = []
    for rec in report.records:
        entry = entries_by_key.get(rec.key)
        if not rec.flagged or entry is None:
            continue
        records.append(
            ValidationRecordOut(
                key=rec.key,
                status=rec.status,
                doi=rec.doi,
                suggested_doi=rec.suggested_doi,
                problems=[
                    FieldProblemOut(field=c.field, ours=c.ours, canonical=c.canonical)
                    for c in rec.problems
                ],
                entry=entry_to_dict(entry),
                canonical=entry_to_dict(rec.canonical_entry) if rec.canonical_entry else None,
            )
        )
    return ValidationOut(
        total=report.total, flagged=report.flagged_count, counts=counts, records=records
    )
