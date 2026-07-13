"""Interactive console review of flagged reference corrections.

Renders each reference the online validator flagged and collects one
:class:`~latextify.model.validate.CorrectionDecision` per reference by prompting
the operator (approve / deny / edit / skip-the-rest). The loop takes injectable
``prompt`` and ``echo`` callables so it is unit-testable with scripted input and
carries no TTY assumptions of its own -- the CLI decides when a real terminal is
present before calling it.

Pure decision-collection only: applying the decisions to the bibliography lives
in :func:`latextify.citations.corrections.apply_corrections`, and rewriting /
recompiling lives in the CLI.
"""

from __future__ import annotations

from collections.abc import Callable

from .citations.corrections import EDITABLE_FIELDS, entry_from_dict, entry_to_dict
from .model.refs import RefEntry
from .model.validate import CorrectionDecision, ValidationRecord, ValidationReport

Prompt = Callable[[str], str]
Echo = Callable[[str], None]

# Field-name -> the label shown in the whole-entry editor.
_FIELD_LABELS = {
    "title": "Title",
    "authors": "Authors (Family, Given; ...)",
    "year": "Year",
    "journal": "Journal",
    "volume": "Volume",
    "issue": "Issue",
    "pages": "Pages",
    "doi": "DOI",
}

_STATUS_HEADLINE = {
    "mismatch": "fields disagree with Crossref",
    "dead_doi": "DOI does not resolve in Crossref",
    "doi_suggested": "no DOI in reference (Crossref match found)",
    "unverifiable": "could not verify (no DOI, no confident match)",
}


def describe_record(record: ValidationRecord) -> list[str]:
    """Human-readable summary lines for one flagged reference (no trailing NL)."""
    lines = [f"[{record.key}] {_STATUS_HEADLINE.get(record.status, record.status)}"]
    if record.status == "dead_doi" and record.doi:
        lines.append(f"    current DOI: {record.doi}")
    if record.status == "doi_suggested" and record.suggested_doi:
        lines.append(f"    suggested DOI: {record.suggested_doi}")
    for check in record.problems:
        lines.append(f"    {check.field}: yours “{check.ours}” → Crossref “{check.canonical}”")
    return lines


def _edit_entry(entry: RefEntry, prompt: Prompt, echo: Echo) -> RefEntry:
    """Field-by-field whole-entry edit; blank input keeps the current value."""
    current = entry_to_dict(entry)
    edited = dict(current)
    echo("    Editing entry (press Enter to keep the current value):")
    for field in EDITABLE_FIELDS:
        label = _FIELD_LABELS.get(field, field)
        shown = current.get(field, "")
        response = prompt(f"      {label} [{shown}]: ")
        if response.strip():
            edited[field] = response.strip()
    return entry_from_dict(edited, base=entry)


def review_corrections(
    entries: list[RefEntry],
    report: ValidationReport,
    *,
    prompt: Prompt,
    echo: Echo,
) -> list[CorrectionDecision]:
    """Prompt through every flagged reference, returning the author's decisions.

    Only flagged references are shown. For each: **a**pprove adopts Crossref's
    values, **d**eny keeps it, **e**dit opens the whole-entry editor, **s**kip
    stops the review (remaining references are left untouched). An unrecognized
    answer is treated as deny for that one reference.
    """
    entries_by_key = {e.key: e for e in entries}
    flagged = [r for r in report.records if r.flagged]
    decisions: list[CorrectionDecision] = []
    if not flagged:
        return decisions

    echo(f"\n{len(flagged)} reference(s) need review.\n")
    for index, record in enumerate(flagged, start=1):
        entry = entries_by_key.get(record.key)
        echo(f"— {index}/{len(flagged)} " + "-" * 40)
        for line in describe_record(record):
            echo(line)
        if entry is None:
            # No entry to edit/approve against (should not happen); skip safely.
            echo("    (entry not found; skipping)")
            continue
        choice = prompt("  [a]pprove / [d]eny / [e]dit / [s]kip rest: ").strip().lower()
        if choice.startswith("s"):
            break
        if choice.startswith("a"):
            decisions.append(CorrectionDecision(key=record.key, action="approve"))
        elif choice.startswith("e"):
            edited = _edit_entry(entry, prompt, echo)
            decisions.append(
                CorrectionDecision(key=record.key, action="edit", edited_entry=edited)
            )
        else:
            decisions.append(CorrectionDecision(key=record.key, action="deny"))
    return decisions
