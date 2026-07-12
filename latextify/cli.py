"""Command-line interface.

Current surface (plan items 5, 16, 18, 19, 20, 21, 23):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear] [--pdf] [--report/--no-report] \\
        [--supplement si.docx]  # Supplementary Material (item 21)
    latextify batch folder --journal J [--citation-style S] [--pdf] \\
        [--output output] [--recursive]          # batch conversion (item 20)
    latextify journals              # list registered journal templates (item 18)
    latextify equations paper.docx [--output DIR] [--pdf]  # equation audit (item 23)
    latextify gui [--port 8501] [--no-browser] [--workdir DIR]  # local web GUI (item 19)

Planned (later items):

    latextify preflight paper.docx  # validation report only, no conversion
"""

from __future__ import annotations

import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import typer

from latextify.audit.equations import write_equation_audit
from latextify.compile.tectonic import TectonicNotAvailableError, compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.report.render import write_report
from latextify.templates import loader
from latextify.templates.loader import ManifestError, load

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _callback() -> None:
    """LaTeXtify: convert Word manuscripts into journal-ready LaTeX projects."""


@app.command()
def convert(
    docx_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source .docx manuscript to convert."
    ),
    journal: str = typer.Option(
        ..., "--journal", "-j", help="Target journal template (e.g. 'revtex4-2')."
    ),
    output: Path = typer.Option(
        Path("output"), "--output", "-o", help="Output root directory."
    ),
    citation_style: str = typer.Option(
        None,
        "--citation-style",
        help="Citation mode override: numeric|authoryear (journal-dependent).",
    ),
    crossref_mailto: str = typer.Option(
        None,
        "--crossref-mailto",
        help="Contact email sent to Crossref when reconstructing plain-text "
        "citations (documents with no citation field codes). Recommended.",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Compile LaTeX to PDF after emission using Tectonic.",
    ),
    report: bool = typer.Option(
        True,
        "--report/--no-report",
        help="Generate report.md (default: on).",
    ),
    supplement: Path = typer.Option(
        None,
        "--supplement",
        exists=True,
        readable=True,
        help="Second .docx to emit as Supplementary Material: writes "
        "supplement.tex (S-numbered figures/tables/equations/sections) "
        "sharing this project's figures/ and references.bib with the main "
        "document.",
    ),
) -> None:
    """Convert DOCX_PATH into a journal-ready LaTeX project under output/<journal>/."""
    try:
        journal_obj = load(journal)
        result = emit_project(
            docx_path,
            journal,
            output,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
            report=report,
            supplement_docx_path=supplement,
        )
    except ManifestError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        # Every ingest-boundary module (preflight, metadata_guess, pandoc)
        # raises a clean ValueError naming the problem for a corrupt or
        # unsupported .docx -- never let it surface as a raw, unhandled
        # traceback here (ManifestError is itself a ValueError subclass, so
        # this branch also covers it defensively).
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_dir}")
    if not result.main_tex_written:
        typer.echo("main.tex already existed -- left untouched (edit it directly)")
    if result.supplement is not None and not result.supplement.supplement_tex_written:
        typer.echo("supplement.tex already existed -- left untouched (edit it directly)")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")
    if result.supplement is not None:
        for warning in result.supplement.warnings:
            typer.echo(f"warning: {warning.message}")

    # Compile step (item 16: CLI wiring for --pdf flag; item 21 compiles the
    # supplement too when one was emitted).
    compile_result = None
    supplement_compile_result = None
    if pdf:
        try:
            vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
            compile_result = compile_document(
                result.main_tex_path,
                tectonic_path=ensure_tectonic(),
                vendor_dir=vendor_dir,
            )
            if compile_result.success:
                typer.echo(f"compiled {compile_result.pdf_path}")
            else:
                typer.echo("compilation failed (see report.md)", err=True)

            if result.supplement is not None:
                supplement_compile_result = compile_document(
                    result.supplement.supplement_tex_path,
                    tectonic_path=ensure_tectonic(),
                    vendor_dir=vendor_dir,
                )
                if supplement_compile_result.success:
                    typer.echo(f"compiled {supplement_compile_result.pdf_path}")
                else:
                    typer.echo("supplement compilation failed (see report.md)", err=True)
        except Exception as exc:
            typer.echo(f"error: compilation failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    # Update report with compile diagnostics if compilation ran.
    if report and compile_result is not None:
        write_report(
            result.report_path or result.output_dir / "report.md",
            preflight=None,  # Already included in initial report
            emit_result=result,
            reconciliation=None,  # Already included
            compile_result=compile_result,
            supplement=result.supplement,
        )

    # Exit code policy (item 16): nonzero if compile errors (item 21: either document).
    exit_code = 0
    if compile_result is not None and not compile_result.success:
        exit_code = 1
    if supplement_compile_result is not None and not supplement_compile_result.success:
        exit_code = 1

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


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


@app.command()
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


@app.command()
def journals() -> None:
    """List registered journal templates with their available citation modes."""
    discovered = loader.discover()
    if not discovered:
        typer.echo("No journals registered.")
        return

    for journal_name in sorted(discovered.keys()):
        try:
            journal = loader.load(journal_name)
            modes = sorted(journal.bib_modes.keys())
            modes_str = ", ".join(modes)
            typer.echo(f"{journal_name}: {modes_str}")
        except ManifestError as exc:
            typer.echo(f"{journal_name}: error loading manifest: {exc}", err=True)


@app.command()
def equations(
    docx_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source .docx manuscript to audit."
    ),
    output: Path = typer.Option(
        Path("equation_audit"),
        "--output",
        "-o",
        help="Directory to write equations_audit.md (and audit.pdf with --pdf) into.",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Also compile a numbered audit.pdf via Tectonic, for side-by-side "
        "comparison against the Word document.",
    ),
) -> None:
    """Extract every equation in DOCX_PATH and write a Word-vs-LaTeX conversion audit.

    There is no way to render a Word equation object without Word itself, so
    the comparison is textual: each equation's source paragraph snippet is
    paired with pandoc's own converted LaTeX in equations_audit.md (and,
    with --pdf, a numbered audit.pdf) for the user to scan against the
    original .docx.
    """
    tectonic_path = None
    if pdf:
        try:
            tectonic_path = ensure_tectonic()
        except TectonicNotAvailableError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    try:
        result = write_equation_audit(
            docx_path, output, compile_pdf=pdf, tectonic_path=tectonic_path
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # ValueError -- corrupt/unsupported docx (extraction, ingest boundary).
        # OSError/SubprocessError -- compile_document's own documented escape
        # hatches when --pdf is set (a hung compile raises
        # subprocess.TimeoutExpired, a tectonic binary present but unable to
        # execute raises OSError) -- never let either reach the user as a raw
        # traceback.
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.audit_md_path}")
    if result.result.count_mismatch:
        typer.echo(
            f"warning: raw OMML equation count ({result.result.raw_omml_count}) != "
            f"pandoc-converted count ({result.result.converted_count}) -- pandoc likely "
            "dropped, merged, or invented an equation; see equations_audit.md",
            err=True,
        )

    exit_code = 0
    if pdf:
        if result.audit_pdf_path is not None:
            typer.echo(f"compiled {result.audit_pdf_path}")
        else:
            typer.echo("audit.pdf failed to compile (see equations_audit.md)", err=True)
            exit_code = 1
        for status in result.compile_statuses:
            if not status.ok:
                typer.echo(
                    f"warning: equation {status.index + 1} failed to compile standalone: "
                    f"{status.message}",
                    err=True,
                )

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def gui(
    port: int = typer.Option(
        8501, "--port", help="Port to bind the local GUI server to."
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't automatically open a browser window.",
    ),
    workdir: Path = typer.Option(
        None,
        "--workdir",
        help="Directory for per-conversion working files (default: a fresh temp dir).",
    ),
) -> None:
    """Start a local web GUI (drag-and-drop, journal picker, PDF preview).

    Binds 127.0.0.1 only -- this is a local tool and uploaded manuscripts
    are private, never exposed on the network. Requires the optional 'gui'
    extra (fastapi, uvicorn, python-multipart); see the error message below
    if it isn't installed.
    """
    try:
        import uvicorn

        from latextify.gui.server import create_app
    except ImportError as exc:
        typer.echo(
            "error: the GUI requires optional dependencies that aren't installed.\n"
            "Install them with:\n"
            "  uv pip install 'latextify[gui]'\n"
            "or:\n"
            "  pip install 'latextify[gui]'",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    application = create_app(workdir=workdir)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"LaTeXtify GUI running at {url} (Ctrl+C to stop)")
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(application, host="127.0.0.1", port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
