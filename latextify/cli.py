"""Command-line interface.

Current surface (plan item 5's minimal wiring; items 16/18 extend this):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear]

Planned (later items):

    latextify journals              # list registered journal templates
    latextify preflight paper.docx  # validation report only, no conversion
"""

from __future__ import annotations

from pathlib import Path

import typer

from latextify.emit.project import emit_project
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
) -> None:
    """Convert DOCX_PATH into a journal-ready LaTeX project under output/<journal>/."""
    try:
        result = emit_project(docx_path, journal, output, citation_style=citation_style)
    except ManifestError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_dir}")
    if not result.main_tex_written:
        typer.echo("main.tex already existed -- left untouched (edit it directly)")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
