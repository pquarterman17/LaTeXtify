"""Render the consolidated conversion report as markdown.

Aggregates findings from every stage (preflight, citations, figures, compile,
and emit-stage warnings) into one deterministic report.md. Sections are
ordered, content sorted within each section, and empty sections display "none"
rather than disappearing so diffs are meaningful across runs. All free text
(messages, captions, diagnostics) is flattened to a single line before
insertion so a stray newline can't inject broken markdown structure.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from latextify.model.compile import CompileResult
from latextify.model.emit import EmitResult
from latextify.model.preflight import PreflightReport
from latextify.model.reconcile import ReconciliationReport


def _flatten(text: str) -> str:
    """Collapse line breaks so one record stays one markdown line/quote.

    Messages, captions, and diagnostics are arbitrary user/pipeline text; a
    raw newline would split a ``- ``/``> `` item across lines and mangle the
    report's structure (markdown-format injection). Newlines become single
    spaces; other spacing is preserved.
    """
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def render_report(
    *,
    preflight: PreflightReport | None = None,
    emit_result: EmitResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    compile_result: CompileResult | None = None,
) -> str:
    """Render all aggregated findings into a markdown report string.

    Args:
        preflight: source .docx inventory (unsupported constructs, style usage).
        emit_result: project-emission outcome (anchor warnings, figure conversions).
        reconciliation: plain-text citation reconstruction records (only present
            when the document had no citation field codes).
        compile_result: Tectonic compilation outcome (errors, warnings, success).

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
        if compile_result.success:
            lines.append("✓ **Success** — PDF compiled without errors.\n")
        else:
            lines.append("✗ **Failed** — see diagnostics below.\n")

        if compile_result.diagnostics:
            # Sort by severity (ERROR, WARNING) then by location
            sorted_diags = sorted(
                compile_result.diagnostics,
                key=lambda d: (
                    0 if d.severity.value == "error" else 1,
                    d.file or "",
                    d.line or 0,
                ),
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
        else:
            lines.append("No diagnostics available.\n")
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

    return "".join(lines)


def write_report(
    output_path: Path,
    *,
    preflight: PreflightReport | None = None,
    emit_result: EmitResult | None = None,
    reconciliation: ReconciliationReport | None = None,
    compile_result: CompileResult | None = None,
) -> Path:
    """Render and write the report to a file.

    Args:
        output_path: destination for report.md (must be a file path, not a directory).
        preflight, emit_result, reconciliation, compile_result: see :func:`render_report`.

    Returns:
        The path to the written report file.
    """
    report_text = render_report(
        preflight=preflight,
        emit_result=emit_result,
        reconciliation=reconciliation,
        compile_result=compile_result,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    return output_path
