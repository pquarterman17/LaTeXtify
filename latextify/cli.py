"""Command-line interface.

Current surface (plan item 5's minimal wiring; items 16/18 extend this):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear]
    latextify journals              # list registered journal templates (item 18)

Planned (later items):

    latextify preflight paper.docx  # validation report only, no conversion
"""

from __future__ import annotations

from pathlib import Path

import typer

from latextify.emit.project import emit_project
from latextify.templates import loader
from latextify.templates.loader import ManifestError

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
) -> None:
    """Convert DOCX_PATH into a journal-ready LaTeX project under output/<journal>/."""
    try:
        result = emit_project(
            docx_path,
            journal,
            output,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
        )
    except ManifestError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_dir}")
    if not result.main_tex_written:
        typer.echo("main.tex already existed -- left untouched (edit it directly)")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
