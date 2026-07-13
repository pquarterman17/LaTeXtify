"""Render the consolidated conversion report as markdown.

Aggregates findings from every stage (preflight, citations, figures, compile,
and emit-stage warnings) into one deterministic report.md. Sections are
ordered, content sorted within each section, and empty sections display "none"
rather than disappearing so diffs are meaningful across runs. All free text
(messages, captions, diagnostics) is flattened to a single line before
insertion so a stray newline can't inject broken markdown structure.

The Supplement section (plan item 21) always renders -- "_None_ (use
--supplement ...)" when no SI was emitted this run, so the section's
presence/absence never depends on which arguments happened to be passed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from latextify.model.compile import CompileResult
from latextify.model.emit import EmitResult, SupplementResult
from latextify.model.preflight import PreflightReport
from latextify.model.reconcile import ReconciliationReport
from latextify.model.validate import ValidationRecord, ValidationReport


def _flatten(text: str) -> str:
    """Collapse line breaks so one record stays one markdown line/quote.

    Messages, captions, and diagnostics are arbitrary user/pipeline text; a
    raw newline would split a ``- ``/``> `` item across lines and mangle the
    report's structure (markdown-format injection). Newlines become single
    spaces; other spacing is preserved.
    """
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


# Human-readable label + marker per flagged validation status. Verified and
# unchecked references are only counted (not listed), so they are absent here.
_VALIDATION_LABELS = {
    "mismatch": ("⚠️", "field mismatch vs Crossref"),
    "dead_doi": ("⚠️", "DOI does not resolve in Crossref"),
    "doi_suggested": ("💡", "no DOI in reference — Crossref match found"),
    "unverifiable": ("❔", "no DOI and no confident Crossref match"),
}
# Order flagged records worst-first, then by key for stable diffs.
_VALIDATION_ORDER = {"dead_doi": 0, "mismatch": 1, "doi_suggested": 2, "unverifiable": 3}


def _render_validation_record(record: ValidationRecord) -> list[str]:
    """One flagged reference: header line plus any field-level detail."""
    marker, label = _VALIDATION_LABELS.get(record.status, ("⚠️", record.status))
    out = [f"- {marker} `{record.key}` — {label}"]
    if record.status == "dead_doi" and record.doi:
        out[0] += f" (`{record.doi}`)"
    if record.status == "doi_suggested" and record.suggested_doi:
        out[0] += f": add `{record.suggested_doi}`"
    out[0] += "\n"
    for check in record.problems:
        out.append(
            f'  - {check.field}: ours "{_flatten(check.ours)}" '
            f'≠ Crossref "{_flatten(check.canonical)}"\n'
        )
    return out


def _render_validation(validation: ValidationReport) -> list[str]:
    """Render the Reference Validation section body (excludes the header)."""
    lines: list[str] = []
    if not validation.any_checked:
        lines.append(
            "_Requested, but Crossref was unreachable_ — no references were "
            "verified (network required).\n"
        )
        return lines
    # Summary line: counts per status, only the non-zero ones.
    order = ["verified", "mismatch", "dead_doi", "doi_suggested", "unverifiable", "unchecked"]
    labels = {
        "verified": "verified",
        "mismatch": "field mismatch",
        "dead_doi": "dead DOI",
        "doi_suggested": "DOI suggested",
        "unverifiable": "unverifiable",
        "unchecked": "unchecked (offline)",
    }
    parts = [f"{validation.count(s)} {labels[s]}" for s in order if validation.count(s)]
    lines.append(
        f"Checked {validation.total} reference(s) against Crossref: " + ", ".join(parts) + ".\n"
    )
    flagged = sorted(
        (r for r in validation.records if r.flagged),
        key=lambda r: (_VALIDATION_ORDER.get(r.status, 9), r.key),
    )
    if flagged:
        for record in flagged:
            lines.extend(_render_validation_record(record))
    else:
        lines.append("All checked references verified cleanly. ✓\n")
    return lines


def _render_compile_outcome(lines: list[str], result: CompileResult, *, label: str) -> None:
    """Append one document's compile outcome + sorted diagnostics to ``lines``.

    Shared by the main and supplement documents so a supplement failure is
    reported exactly like the main one (heading, ✓/✗ status, ERROR/WARNING
    diagnostics sorted by severity then location).
    """
    lines.append(f"**{label}:** ")
    if result.success:
        lines.append("✓ compiled without errors.\n")
    else:
        lines.append("✗ failed — see diagnostics below.\n")
    if result.diagnostics:
        sorted_diags = sorted(
            result.diagnostics,
            key=lambda d: (0 if d.severity.value == "error" else 1, d.file or "", d.line or 0),
        )
        for diag in sorted_diags:
            sev = diag.severity.value.upper()
            loc = ""
            if diag.file or diag.line:
                loc = f" ({diag.file}"
                if diag.line:
                    loc += f":{diag.line}"
                loc += ")"
            lines.append(f"**[{sev}]{loc}:** {_flatten(diag.message)}\n")
    elif not result.success:
        lines.append("No diagnostics available.\n")


def render_report(
    *,
    preflight: PreflightReport | None = None,
    emit_result: EmitResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    compile_result: CompileResult | None = None,
    supplement: SupplementResult | None = None,
    supplement_compile: CompileResult | None = None,
    validation: ValidationReport | None = None,
) -> str:
    """Render all aggregated findings into a markdown report string.

    Args:
        preflight: source .docx inventory (unsupported constructs, style usage).
        emit_result: project-emission outcome (anchor warnings, figure conversions).
        reconciliation: plain-text citation reconstruction records (only present
            when the document had no citation field codes).
        compile_result: Tectonic compilation outcome (errors, warnings, success).
        supplement: supplementary-material emission outcome (plan item 21),
            only present when ``latextify convert`` was given ``--supplement``.
        validation: online reference-validation outcome, only present when
            ``latextify convert`` was given ``--check-references``.

    Returns:
        Markdown string (deterministically ordered, stable across runs).
    """
    lines: list[str] = []

    # Header
    lines.append("# Conversion Report\n")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")

    # Preflight findings
    lines.append("## Preflight Findings\n")
    if preflight and preflight.findings:
        # Sort by severity (ERROR, WARN, INFO) then by paragraph location
        sorted_findings = sorted(
            preflight.findings,
            key=lambda f: (
                {"error": 0, "warn": 1, "info": 2}[f.severity.value],
                f.location.paragraph_index,
            ),
        )
        for finding in sorted_findings:
            lines.append(
                f"**[{finding.severity.upper()}]** "
                f"({finding.detector}, ¶{finding.location.paragraph_index}): "
                f"{_flatten(finding.message)}\n"
            )
            if finding.location.text_snippet:
                # Indent snippet so it reads as a quote
                snippet = finding.location.text_snippet.replace("\n", "\n> ")
                lines.append(f"> {snippet}\n")
    else:
        lines.append("_None_\n")

    # Citation extraction (plain-text reconstruction only)
    lines.append("\n## Citation Extraction\n")
    if reconciliation and reconciliation.records:
        lines.append(
            f"Reconstructed {reconciliation.matched_count}/{reconciliation.total} "
            f"references via Crossref "
            f"({reconciliation.matched_fraction * 100:.0f}% matched):\n"
        )
        # Sort by verify flag (flagged first), then by reference number (if numeric),
        # then by key for stable ordering.
        sorted_records = sorted(
            reconciliation.records,
            key=lambda r: (
                not r.verify,  # verify=True sorts first (Falses sort before Trues)
                r.ref_number if r.ref_number is not None else 999999,
                r.key,
            ),
        )
        for record in sorted_records:
            marker = ""
            if record.verify:
                marker = " ⚠️ VERIFY"
            score_str = f"score={record.score:.2f}" if record.source == "crossref" else ""
            doi_str = f"doi={record.doi}" if record.doi else ""
            meta = " ".join(s for s in [score_str, doi_str] if s)
            meta_suffix = f", {meta}" if meta else ""
            lines.append(f"- `{record.key}` ({record.source}{meta_suffix}){marker}\n")
            if record.matched_title:
                lines.append(f"  Title: {record.matched_title}\n")
    elif emit_result and emit_result.citation_count > 0:
        lines.append(
            f"Extracted {emit_result.citation_count} citations from field codes "
            "(Zotero/Mendeley/EndNote/Word-native).\n"
        )
    else:
        lines.append("_None_\n")

    # Reference validation (online Crossref check; opt-in --check-references).
    # Placed right after citation extraction: both concern the bibliography, and
    # the validation refines what extraction produced. Always renders so its
    # absence in the report unambiguously means "not requested".
    lines.append("\n## Reference Validation\n")
    if validation is not None:
        lines.extend(_render_validation(validation))
    else:
        lines.append("_Not checked_ (use `--check-references` to validate online).\n")

    # Figures
    lines.append("\n## Figures\n")
    if emit_result and emit_result.figures:
        # Sort by figure number (always ascending)
        sorted_figures = sorted(emit_result.figures, key=lambda f: f.number)
        for figure in sorted_figures:
            source_label = figure.source.value.upper()
            conv_note = f" — {figure.conversion_note}" if figure.conversion_note else ""
            lines.append(f"**Fig {figure.number}** ({source_label}){conv_note}\n")
            if figure.caption:
                lines.append(f"> {_flatten(figure.caption)}\n")
    else:
        lines.append("_None_\n")

    # Compilation
    lines.append("\n## Compilation\n")
    if compile_result:
        _render_compile_outcome(lines, compile_result, label="Main")
        # Supplement compile is a SEPARATE document: report its own outcome and
        # diagnostics so a supplement failure is visible even when the main PDF
        # compiled cleanly (previously only the main outcome was ever surfaced).
        if supplement_compile is not None:
            _render_compile_outcome(lines, supplement_compile, label="Supplement")
    else:
        lines.append("_Not compiled_ (use `--pdf` to compile).\n")

    # Emit-stage warnings (anchor resolution, figure conversion, citation
    # linkage gaps, bibliography migration). Aggregated here so the report is
    # the single consolidated record -- previously EmitResult.warnings never
    # reached report.md at all. Sorted for stable diffs across runs.
    lines.append("\n## Warnings\n")
    if emit_result and emit_result.warnings:
        for message in sorted(_flatten(w.message) for w in emit_result.warnings):
            lines.append(f"- {message}\n")
    else:
        lines.append("_None_\n")

    # Supplementary material (plan item 21): files written, S-figure/citation
    # counts, and any SI-specific warnings (preflight/anchor/conversion),
    # each already prefixed "supplement: " at the source so they read clearly
    # even scanned in isolation from the rest of the report.
    lines.append("\n## Supplement\n")
    if supplement is not None:
        status = (
            "written"
            if supplement.supplement_tex_written
            else "already existed (left untouched)"
        )
        lines.append(f"`supplement.tex` {status}.\n")
        lines.append(f"S-figures: {supplement.figure_count}.\n")
        lines.append(
            f"SI citations: {supplement.citation_count} "
            f"({supplement.new_reference_count} new reference(s) added to "
            "references.bib; the rest were deduplicated against the main "
            "document's bibliography).\n"
        )
        if supplement.warnings:
            for message in sorted(_flatten(w.message) for w in supplement.warnings):
                lines.append(f"- {message}\n")
        else:
            lines.append("No supplement-specific warnings.\n")
    else:
        lines.append("_None_ (use `--supplement` to add supplementary material).\n")

    return "".join(lines)


def write_report(
    output_path: Path,
    *,
    preflight: PreflightReport | None = None,
    emit_result: EmitResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    compile_result: CompileResult | None = None,
    supplement: SupplementResult | None = None,
    supplement_compile: CompileResult | None = None,
    validation: ValidationReport | None = None,
) -> Path:
    """Render and write the report to a file.

    Args:
        output_path: destination for report.md (must be a file path, not a directory).
        preflight, emit_result, reconciliation, compile_result, supplement,
            supplement_compile, validation: see :func:`render_report`.

    Returns:
        The path to the written report file.
    """
    report_text = render_report(
        preflight=preflight,
        emit_result=emit_result,
        reconciliation=reconciliation,
        compile_result=compile_result,
        supplement=supplement,
        supplement_compile=supplement_compile,
        validation=validation,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    return output_path
