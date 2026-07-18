"""Docx sanitizer command (plan item 3, FORMATS_AND_PRIVACY), split out of
latextify.cli.

``clean`` is a plain function here; ``latextify.cli`` registers it on the
shared Typer ``app`` via ``app.command(name="clean")(clean)`` -- same pattern
as ``latextify.cli_batch.batch`` and ``latextify.cli_equations.equations``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from latextify.ingest.docx_clean import sanitize_docx


def clean(
    src_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source .docx manuscript to sanitize."
    ),
    dest_path: Path = typer.Argument(
        ..., help="Path to write the sanitized copy to."
    ),
) -> None:
    """Write a metadata/review-stripped copy of SRC_PATH to DEST_PATH.

    Accepts tracked changes (insertions kept, deletions dropped), deletes
    comments, drops hidden (``w:vanish``) runs, strips docProps (author,
    company, edit time, custom properties, saved thumbnail), and scrubs
    ``settings.xml``'s rsid index -- see
    :mod:`latextify.ingest.docx_clean`'s module docstring for the full list
    and known gaps.
    """
    try:
        report = sanitize_docx(src_path, dest_path)
    except ValueError as exc:
        # sanitize_docx raises a clean ValueError naming the problem for a
        # missing/wrong-extension/corrupt .docx -- never a raw traceback.
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {dest_path}")
    typer.echo(
        f"accepted {report.tracked_changes_accepted} tracked change(s), "
        f"removed {report.comments_removed} comment(s), "
        f"removed {report.hidden_runs_removed} hidden run(s); "
        + ("docProps stripped" if report.docprops_stripped else "no docProps found")
        + (", rsids scrubbed" if report.rsids_scrubbed else "")
    )
