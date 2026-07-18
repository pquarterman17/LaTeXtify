"""Equation audit command (plan item 23), split out of latextify.cli.

``equations`` is a plain function here; ``latextify.cli`` registers it on the
shared Typer ``app`` via ``app.command()(equations)`` so this module stays
free of the app object (and the cli module stays under its size ceiling) --
same pattern as ``latextify.cli_batch.batch``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from latextify.audit.equations import write_equation_audit
from latextify.compile.tectonic import TectonicNotAvailableError, ensure_tectonic


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
