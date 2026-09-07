"""Turning a manuscript's figure inventory into the GUI's response body.

The mapping half of ``POST /api/figures``, kept out of
:mod:`latextify.gui.uploads_routes` for the same reason
:mod:`latextify.gui.validation_view` sits beside its route: the route owns
upload handling and HTTP status, this owns the shape of what comes back, and
neither file has to grow to hold both.

Mirrors :mod:`latextify.cli_figures`, which prints the same facts as a table.
Both call :func:`latextify.figures.inventory.inventory` on the result of the
same extraction and override resolution a conversion runs, so the browser, the
terminal and the conversion never disagree about which figure is number 3.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from latextify.figures.caption_gaps import caption_gaps
from latextify.figures.extract import extract_figures
from latextify.figures.inventory import FigureFacts, inventory
from latextify.figures.override import resolve_overrides
from latextify.gui.schemas import FigureFactsModel, FiguresResponse


def _as_model(facts: FigureFacts) -> FigureFactsModel:
    """One inventory record as its wire model."""
    return FigureFactsModel(
        number=facts.number,
        caption=facts.caption,
        source=facts.source.value,
        kind=facts.kind.value,
        is_vector=facts.is_vector,
        needs_attention=facts.needs_attention,
        wide=facts.wide,
        in_table=facts.in_table,
        width=facts.measurement.width if facts.measurement else None,
        height=facts.measurement.height if facts.measurement else None,
        dpi=facts.dpi,
        print_width_inches=facts.print_width_inches,
    )


def build_figures_response(docx_path: Path, *, min_print_dpi: int) -> FiguresResponse:
    """Describe ``docx_path``'s figures for the browser.

    Media is extracted into a temporary directory discarded on the way out --
    this reports on a manuscript, it does not convert one. Note that no
    override tier applies here in practice: the upload is staged alone in a
    session directory, with no ``figures/`` folder or ``figures.yaml`` beside
    it, so every figure reports as ``embedded``. That is the honest answer for
    an uploaded file, and it is what the panel needs -- what is in the document
    right now, so the author can decide what to supply.
    """
    with tempfile.TemporaryDirectory(prefix="latextify-figures-") as tmp:
        figures = resolve_overrides(extract_figures(docx_path, Path(tmp)), docx_path)
        facts = inventory(figures)
    return FiguresResponse(
        figures=[_as_model(f) for f in facts],
        caption_gaps=caption_gaps(docx_path, len(facts)),
        min_print_dpi=min_print_dpi,
    )
