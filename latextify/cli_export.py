"""HTML/Markdown export command (FORMATS_AND_PRIVACY items 4-5), split out of
latextify.cli.

``export`` is a plain function here; ``latextify.cli`` registers it on the
shared Typer ``app`` via ``app.command()(export)`` -- same pattern as
``latextify.cli_batch.batch``, ``latextify.cli_clean.clean``, and
``latextify.cli_equations.equations``.

A dedicated command rather than new ``convert`` flags: ``convert`` emits a
LaTeX PROJECT TREE (``main.tex`` + ``generated/`` + ``figures/`` +
``references.bib``) via a completely different pipeline
(:mod:`latextify.emit.project`); HTML/Markdown export writes ONE
self-contained file via :mod:`latextify.emit.alt_formats` and shares almost
none of ``convert``'s option surface (no journal/columns/anonymize/
supplement -- see that module's docstring for why). Folding it into
``convert`` would mean either silently ignoring most of its 20+ options when
an ``--html``/``--markdown`` flag is passed, or duplicating validation for a
combination that never applies; a separate command with its own small,
honest option surface is the cleaner fit. It also sidesteps ``cli.py``'s own
pinned size ceiling (517 lines, zero headroom at the time this command was
added) entirely.
"""

from __future__ import annotations

from pathlib import Path

import typer

from latextify.emit.alt_formats import export_html, export_markdown

_EXPORTERS = {"html": export_html, "markdown": export_markdown}
_EXTENSIONS = {"html": ".html", "markdown": ".md"}


def export(
    docx_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source manuscript to export."
    ),
    output_format: str = typer.Option(
        ..., "--format", "-f", help="Output format: html or markdown."
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="File to write. Defaults to DOCX_PATH with .html/.md substituted "
        "for its extension.",
    ),
    crossref_mailto: str = typer.Option(
        None,
        "--crossref-mailto",
        help="Contact email sent to Crossref when reconstructing plain-text "
        "citations (documents with no citation field codes).",
    ),
    references: Path = typer.Option(
        None,
        "--references",
        exists=True,
        readable=True,
        help="A .bib/.ris/CSL-JSON/EndNote-XML/.nbib export of your reference "
        "manager, consulted before Crossref on the plain-text citation fallback.",
    ),
) -> None:
    """Export DOCX_PATH to a single self-contained ``.html`` or plain ``.md`` file.

    Reuses the same docx-ingest pipeline and reconciled citations/figures
    ``latextify convert`` uses, without a LaTeX project tree -- see
    ``latextify.emit.alt_formats`` for exactly what is (and is not) carried
    over: figures are embedded (HTML, as base64 data: URIs) or copied
    alongside the output (Markdown); math renders as native MathML (HTML) or
    stays literal ``$...$``/``$$...$$`` (Markdown); a reconciled, numbered
    reference list is appended either way.
    """
    fmt = output_format.strip().lower()
    if fmt not in _EXPORTERS:
        typer.echo(
            f"error: unknown --format '{output_format}' (expected html or markdown)", err=True
        )
        raise typer.Exit(code=1)

    target = output if output is not None else docx_path.with_suffix(_EXTENSIONS[fmt])

    try:
        result = _EXPORTERS[fmt](
            docx_path,
            target,
            crossref_mailto=crossref_mailto,
            references_bib_path=references,
        )
    except ValueError as exc:
        # Every ingest-boundary module (preflight, metadata_guess, pandoc)
        # raises a clean ValueError naming the problem for a corrupt or
        # unsupported manuscript -- never let it surface as a raw traceback.
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_path}")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")
