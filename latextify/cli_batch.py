"""Batch conversion command (plan item 20), split out of latextify.cli.

``batch`` is a plain function here; ``latextify.cli`` registers it on the
shared Typer ``app`` via ``app.command()(batch)`` so this module stays free
of the app object (and the cli module stays under its size ceiling).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer

from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.templates.loader import ManifestError, load

# --------------------------------------------------------------------------- #
# Batch conversion (item 20): convert multiple .docx files per-folder.
# --------------------------------------------------------------------------- #


@dataclass
class _FileResult:
    """Per-file batch conversion result (plan item 20)."""

    docx_path: Path
    stem: str
    status: str  # "ok", "warning", "error"
    pdf_compiled: bool
    warning_count: int
    error_message: str | None


def _convert_single_file(
    docx_path: Path,
    journal_name: str,
    output_root: Path,
    *,
    citation_style: str | None = None,
    crossref_mailto: str | None = None,
    compile_pdf: bool = False,
) -> _FileResult:
    """Convert a single .docx file, returning a per-file result.

    Never raises on convert/compile errors -- returns a _FileResult with
    status="error" and error_message set instead. Warnings are counted but
    don't prevent compilation from proceeding (status="warning").
    """
    stem = docx_path.stem
    try:
        # Load journal once per file to check validity.
        journal_obj = load(journal_name)

        # Emit the LaTeX project into a per-file subdirectory.
        result = emit_project(
            docx_path,
            journal_name,
            output_root / stem,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
            report=True,
        )
        warning_count = len(result.warnings)
        status = "warning" if warning_count > 0 else "ok"

        # Optional PDF compilation (same vendor staging as convert command).
        pdf_compiled = False
        if compile_pdf:
            try:
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                compile_result = compile_document(
                    result.main_tex_path,
                    tectonic_path=ensure_tectonic(),
                    vendor_dir=vendor_dir,
                )
                pdf_compiled = compile_result.success
                if not compile_result.success:
                    # Compile failure is not a hard error in batch mode, but lowers status.
                    status = "warning"
                    warning_count += 1
            except Exception:
                # Compile error (timeout, missing tectonic, etc.) is not fatal in batch.
                status = "warning"
                warning_count += 1

        return _FileResult(
            docx_path=docx_path,
            stem=stem,
            status=status,
            pdf_compiled=pdf_compiled,
            warning_count=warning_count,
            error_message=None,
        )

    except (ManifestError, ValueError, OSError, subprocess.SubprocessError) as exc:
        # Any error from ingest boundary, manifest loading, or system calls.
        return _FileResult(
            docx_path=docx_path,
            stem=stem,
            status="error",
            pdf_compiled=False,
            warning_count=0,
            error_message=str(exc),
        )


def batch(
    folder: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, readable=True,
        help="Source folder containing .docx files to convert."
    ),
    journal: str = typer.Option(
        ..., "--journal", "-j", help="Target journal template (e.g. 'revtex4-2')."
    ),
    output: Path = typer.Option(
        Path("output"), "--output", "-o",
        help="Root output directory; each .docx gets a per-stem subdirectory."
    ),
    citation_style: str = typer.Option(
        None, "--citation-style",
        help="Citation mode override: numeric|authoryear (journal-dependent)."
    ),
    crossref_mailto: str = typer.Option(
        None, "--crossref-mailto",
        help="Contact email for Crossref (plain-text citation reconstruction)."
    ),
    pdf: bool = typer.Option(
        False, "--pdf", help="Compile each LaTeX project to PDF."
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r",
        help="Walk subdirectories recursively; non-recursive by default."
    ),
) -> None:
    """Convert a batch of .docx files in FOLDER, one project per file.

    Output structure: each .docx creates a per-stem subdirectory under
    output/<stem>/<journal>/, preserving independent conversions. One failed
    file does not stop the batch -- all files are processed. Exit code is 0
    if every file succeeded (warnings allowed), 1 if any file errored.

    Temporary Word files (like ~$name.docx) are skipped automatically.
    """
    # Collect .docx files, skipping temp files.
    docx_files: list[Path] = []
    if recursive:
        pattern = "**/*.docx"
    else:
        pattern = "*.docx"

    for docx in sorted(folder.glob(pattern)):
        # Skip Word temp files (start with ~$).
        if docx.name.startswith("~$"):
            continue
        docx_files.append(docx)

    if not docx_files:
        typer.echo(f"No .docx files found in {folder}" +
                   (" (recursive)" if recursive else ""))
        return  # Exit 0 for empty folder.

    # Convert each file, collecting results.
    results: list[_FileResult] = []
    for docx_path in docx_files:
        result = _convert_single_file(
            docx_path,
            journal,
            output,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
            compile_pdf=pdf,
        )
        results.append(result)

    # Print summary table.
    typer.echo()
    typer.echo("Batch summary:")
    typer.echo("-" * 80)
    for result in results:
        pdf_str = "yes" if result.pdf_compiled else "no"
        warning_str = f"({result.warning_count})" if result.warning_count > 0 else ""
        status_line = result.status.upper()
        if result.error_message:
            status_line += f": {result.error_message[:40]}"
        typer.echo(f"{result.stem:30} {status_line:15} {warning_str:10} {pdf_str:5}")

    # Write batch_summary.md with full error text.
    summary_path = output / "batch_summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    summary_lines = [
        "# Batch Conversion Summary\n",
        f"Journal: {journal}\n",
        f"Citation style: {citation_style or 'default'}\n",
        f"PDF compilation: {'yes' if pdf else 'no'}\n",
        f"Total files: {len(results)}\n",
        f"Succeeded: {sum(1 for r in results if r.status == 'ok')}\n",
        f"Warnings: {sum(1 for r in results if r.status == 'warning')}\n",
        f"Errors: {sum(1 for r in results if r.status == 'error')}\n",
        "\n",
        "## Per-File Status\n",
        "\n",
        "| File | Status | Warnings | PDF | Error |\n",
        "|------|--------|----------|-----|-------|\n",
    ]

    for result in results:
        pdf_cell = "✓" if result.pdf_compiled else "✗"
        error_cell = result.error_message[:60] if result.error_message else ""
        warning_cell = str(result.warning_count) if result.warning_count > 0 else ""
        summary_lines.append(
            f"| {result.stem} | {result.status} | {warning_cell} | {pdf_cell} | "
            f"{error_cell} |\n"
        )

    summary_path.write_text("".join(summary_lines), encoding="utf-8")
    typer.echo()
    typer.echo(f"wrote {summary_path}")

    # Exit code: 1 if any file errored.
    exit_code = 1 if any(r.status == "error" for r in results) else 0
    if exit_code != 0:
        raise typer.Exit(code=exit_code)
