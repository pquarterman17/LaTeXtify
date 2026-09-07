"""``latextify figures``: what each figure in a manuscript is, before converting.

A plain function here; :mod:`latextify.cli` registers it on the shared Typer
``app`` -- the same pattern as :mod:`latextify.cli_batch` and
:mod:`latextify.cli_privacy`.

This exists because the override mechanism it serves is keyed by figure
NUMBER. Dropping ``figures/fig3.pdf`` beside a manuscript replaces figure 3 --
but figure 3 is the third image in document order, which is not necessarily
the one captioned "Figure 3", and nothing previously told an author which was
which. Naming the file therefore meant guessing, and a wrong guess silently
swaps two figures in a submission. Listing the figures with their captions,
formats and print resolution turns that guess into a lookup.

Writes nothing. It runs the same extraction and override resolution the
conversion does, so what it reports is what a conversion would use.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import typer

from latextify.figures.caption_gaps import caption_gaps
from latextify.figures.extract import extract_figures
from latextify.figures.inventory import (
    MIN_PRINT_DPI,
    FigureFacts,
    FigureKind,
    inventory,
)
from latextify.figures.override import resolve_overrides
from latextify.model.figure import FigureSource

# The 300 DPI figure quoted in this command's help text is
# latextify.figures.inventory.MIN_PRINT_DPI; tests/test_figures_inventory.py
# pins the two together so the prose cannot drift from the threshold.

#: Caption text is truncated to this in the table so a row stays on one line.
_CAPTION_WIDTH = 44

#: What each ``FigureKind`` is called in the table's KIND column.
_KIND_LABEL = {
    FigureKind.VECTOR: "vector",
    FigureKind.RASTER: "raster",
    FigureKind.RASTER_IN_PDF: "raster-pdf",
    FigureKind.UNKNOWN: "unknown",
}


def _truncate(text: str, width: int) -> str:
    """One-line, width-bounded caption text for the table."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    return flat[: width - 3].rstrip() + "..."


def _size_cell(facts: FigureFacts) -> str:
    """The SIZE column: pixels for a raster, points for a measured vector."""
    measurement = facts.measurement
    if measurement is None:
        return "-"
    if measurement.pixel_width is not None and facts.kind is FigureKind.RASTER:
        return f"{measurement.width:.0f}x{measurement.height:.0f}"
    return f"{measurement.width:.0f}x{measurement.height:.0f}pt"


def _dpi_cell(facts: FigureFacts) -> str:
    """The DPI column: blank for vector art, flagged when below the floor.

    The LOW flag is decided on the raw value and only then rounded for
    display. Rounding first made 299.7 DPI print as an unflagged "300" while
    the summary below still listed that figure as needing replacement.
    """
    dpi = facts.dpi
    if dpi is None:
        return "-" if facts.is_vector else "?"
    return f"{dpi:.0f}{' LOW' if facts.needs_attention else ''}"


def _as_dict(facts: FigureFacts) -> dict[str, object]:
    """One figure as JSON-serializable data (``--json``)."""
    return {
        "number": facts.number,
        "caption": facts.caption,
        "source": facts.source.value,
        # Only a user-supplied override has a path worth handing to a script.
        # An embedded figure's media is extracted into a temporary directory
        # that is already gone by the time this prints, so reporting it would
        # be a dangling path in an interface documented as scriptable.
        "path": str(facts.path) if facts.source is not FigureSource.EMBEDDED else None,
        "kind": facts.kind.value,
        "is_vector": facts.is_vector,
        "needs_attention": facts.needs_attention,
        "wide": facts.wide,
        "in_table": facts.in_table,
        "width": facts.measurement.width if facts.measurement else None,
        "height": facts.measurement.height if facts.measurement else None,
        "dpi": facts.dpi,
        "print_width_inches": facts.print_width_inches,
    }


def _describe_manuscript(docx_path: Path) -> tuple[tuple[FigureFacts, ...], list[int]]:
    """Resolve and describe ``docx_path``'s figures, plus its caption gaps.

    Media is extracted into a temporary directory that is discarded on the way
    out -- this command reports, it does not write into the user's tree.
    """
    with tempfile.TemporaryDirectory(prefix="latextify-figures-") as tmp:
        figures = resolve_overrides(extract_figures(docx_path, Path(tmp)), docx_path)
        facts = inventory(figures)
    return facts, caption_gaps(docx_path, len(facts))


def figures_cmd(
    docx_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Manuscript to list the figures of."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the inventory as JSON instead of a table."
    ),
) -> None:
    """List DOCX_PATH's figures: number, caption, source, format and print DPI.

    Figures are numbered in document order, which is the number an override
    file is named for: figure 3 here is replaced by ``figures/fig3.pdf`` beside
    the manuscript. SOURCE says where the current file came from -- ``embedded``
    for an image pasted into the document, ``override`` or ``manifest`` for one
    you already supplied.

    KIND is what that file actually is. ``raster-pdf`` means a PDF whose content
    is a single image and no text: a screenshot printed to PDF, which has a
    vector file's extension and a raster's limits. DPI is the effective
    resolution at the printed width shown, and is flagged LOW below 300 DPI,
    the floor essentially every publisher states.

    Writes nothing, and exits 0 even when figures need attention -- use
    ``latextify convert --vector-figures`` to gate a conversion on it.
    """
    try:
        facts, gaps = _describe_manuscript(docx_path)
    except ValueError as exc:
        # Exit 1, as convert/export/equations do for the same unreadable-or-
        # unsupported-manuscript ValueError. (`inspect` uses 2, but it grades
        # findings by severity and needs a code that is not "findings found".)
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(
            json.dumps(
                {"figures": [_as_dict(f) for f in facts], "caption_gaps": gaps},
                indent=2,
            )
        )
        return

    if not facts:
        typer.echo("no figures found in the document")
        _echo_gaps(gaps)
        return

    typer.echo(f"{len(facts)} figure(s) in {docx_path.name}\n")
    typer.echo(f"{'FIG':>3}  {'SOURCE':<8}  {'KIND':<10}  {'SIZE':<13}  {'DPI':<8}  CAPTION")
    for f in facts:
        caption = _truncate(f.caption, _CAPTION_WIDTH) or (
            "(in a table cell)" if f.in_table else "(no caption found)"
        )
        typer.echo(
            f"{f.number:>3}  {f.source.value:<8}  {_KIND_LABEL[f.kind]:<10}  "
            f"{_size_cell(f):<13}  {_dpi_cell(f):<8}  {caption}"
        )

    _echo_summary(facts)
    _echo_gaps(gaps)


def _echo_summary(facts: tuple[FigureFacts, ...]) -> None:
    """Close the table with what, if anything, is worth replacing."""
    attention = [f for f in facts if f.needs_attention]
    vector = sum(1 for f in facts if f.is_vector)
    typer.echo(f"\n{vector} of {len(facts)} already vector.")
    if not attention:
        return
    numbers = ", ".join(str(f.number) for f in attention)
    typer.echo(
        f"Figure(s) {numbers} would print below {MIN_PRINT_DPI} DPI. Supply a vector "
        "version of each as figures/fig<N>.pdf (or .eps/.svg) beside the manuscript."
    )


def _echo_gaps(gaps: list[int]) -> None:
    """Report captions with no image behind them, which shift every later number."""
    if not gaps:
        return
    numbers = ", ".join(str(n) for n in gaps)
    typer.echo(
        f"\nCaption(s) with no image: {numbers}. Every figure after a gap is "
        "numbered one lower than its caption says, so check the numbering above "
        "before naming override files."
    )
