"""``inspect`` and the widened ``clean`` command, split out of latextify.cli.

Both are plain functions here; :mod:`latextify.cli` registers them on the
shared Typer ``app`` -- the same pattern as :mod:`latextify.cli_batch` and
:mod:`latextify.cli_equations`.

``clean`` supersedes the docx-only command in :mod:`latextify.cli_clean` by
routing through :mod:`latextify.privacy.registry`, so every registered format
is reachable without restating the accept list here.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from latextify.privacy import inspect_file, sanitize_file, supported_extensions
from latextify.privacy.report import SEVERITIES, Finding


class FailOnSeverity(str, Enum):
    """Severity threshold for exit code 1: fail on this severity or above.

    ``high`` exits 1 only on high-severity findings (METADATA_PRIVACY_PLAN #14).
    ``medium`` exits 1 on medium or high.
    ``low`` exits 1 on any finding.
    ``never`` always exits 0 (useful for CI that logs but does not gate).
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NEVER = "never"

    def should_fail(self, max_severity_found: str | None) -> bool:
        """True if a finding at max_severity_found would trigger exit code 1.

        Args:
            max_severity_found: The highest severity in the report ("high", "medium",
                "low"), or None if no findings exist.

        Returns:
            True if exit code should be 1; False if exit code should be 0.
        """
        if self == FailOnSeverity.NEVER or max_severity_found is None:
            return False
        # Order: "high" > "medium" > "low"
        threshold_rank = SEVERITIES.index(self.value)
        found_rank = SEVERITIES.index(max_severity_found)
        return found_rank <= threshold_rank


_SEVERITY_MARK = {"high": "!!", "medium": " !", "low": "  "}


def _echo_finding(finding: Finding, *, verbose: bool) -> None:
    mark = _SEVERITY_MARK.get(finding.severity, "  ")
    suffix = "" if finding.removable else "   [cannot be fixed automatically]"
    typer.echo(f"  {mark} {finding.summary}{suffix}")
    if verbose:
        typer.echo(f"       {finding.detail}")
        if finding.location:
            typer.echo(f"       location: {finding.location}")


def inspect(
    src_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="File to inspect for metadata."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Explain why each finding matters, and where it lives."
    ),
    fail_on: FailOnSeverity = typer.Option(
        FailOnSeverity.HIGH,
        "--fail-on",
        help="Exit with code 1 if a finding at this severity or above is present. "
        "'never' always exits 0.",
    ),
) -> None:
    """Report the metadata and hidden content in SRC_PATH without changing it.

    Reads the file and writes nothing. Use this to see what a document exposes
    before sending it out, and to verify that a cleaned copy really is clean.

    Exit code: 0 if clean or findings are below --fail-on threshold; 1 if findings
    meet or exceed the threshold (by default, high-severity only); 2 on file errors.
    Always prints findings regardless of --fail-on setting, so the CLI can log
    everything while gating only on severity (METADATA_PRIVACY_PLAN #14).
    """
    try:
        report = inspect_file(src_path)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"{report.path}  ({report.file_format})")
    if report.is_clean:
        typer.echo("  no metadata or hidden content found")
        for warning in report.warnings:
            typer.echo(f"  note: {warning}")
        return

    counts = report.count_by_severity()
    typer.echo(
        f"  {len(report.findings)} finding(s): "
        f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low"
    )
    for finding in report.sorted_findings():
        _echo_finding(finding, verbose=verbose)

    for warning in report.warnings:
        typer.echo(f"  note: {warning}")
    if not verbose:
        typer.echo("  (re-run with --verbose to see why each finding matters)")

    # Compute max severity found (worst-first from sorted_findings).
    max_severity_found = report.sorted_findings()[0].severity if report.findings else None

    # Exit based on threshold, always printing findings above.
    if fail_on.should_fail(max_severity_found):
        raise typer.Exit(code=1)


def clean(
    src_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source file to sanitize."
    ),
    dest_path: Path = typer.Argument(..., help="Path to write the sanitized copy to."),
    keep_notes: bool = typer.Option(
        False, "--keep-notes", help="PowerPoint only: keep speaker notes instead of removing them."
    ),
    keep_color_profile: bool = typer.Option(
        True,
        "--keep-color-profile/--drop-color-profile",
        help="Images only: keep the ICC profile so figure colours are unchanged.",
    ),
) -> None:
    """Write a metadata-stripped copy of SRC_PATH to DEST_PATH.

    Supported: Word, PowerPoint, Excel, PDF and images. What gets removed
    depends on the format -- run `latextify inspect` on the result to confirm.

    Some leaks cannot be fixed by rewriting (text under a PDF redaction box,
    hidden worksheets that formulas depend on, legacy fast-save fragments).
    Those are reported as warnings rather than silently left looking handled.
    """
    try:
        report = sanitize_file(
            src_path,
            dest_path,
            keep_notes=keep_notes,
            keep_color_profile=keep_color_profile,
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {report.dest}  ({report.file_format})")
    if report.removed:
        typer.echo(f"removed {report.total_removed} item(s):")
        for finding in report.sorted_removed():
            typer.echo(f"  - {finding.summary}")
    else:
        typer.echo("nothing to remove; the copy is byte-clean already")

    for warning in report.warnings:
        typer.echo(f"warning: {warning}")


def formats() -> None:
    """List the file formats that inspect and clean understand."""
    typer.echo("Supported formats:")
    for ext in supported_extensions():
        typer.echo(f"  {ext}")
