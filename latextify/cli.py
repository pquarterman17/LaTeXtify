"""Command-line interface.

Current surface (plan items 5, 16, 18):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear] [--pdf] [--report/--no-report]

Planned (later items):

    latextify journals              # list registered journal templates
    latextify preflight paper.docx  # validation report only, no conversion
"""

from __future__ import annotations

from pathlib import Path

import typer

from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.report.render import write_report
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
        )
    except ManifestError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_dir}")
    if not result.main_tex_written:
        typer.echo("main.tex already existed -- left untouched (edit it directly)")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")

    # Compile step (item 16: CLI wiring for --pdf flag).
    compile_result = None
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
        )

    # Exit code policy (item 16): nonzero if compile errors.
    exit_code = 0
    if compile_result is not None and not compile_result.success:
        exit_code = 1

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
