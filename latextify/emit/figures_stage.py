"""One document's whole figure stage: extract, resolve overrides, copy, report.

Split out of :mod:`latextify.emit.project` (2026-09-05) along the seam that
module already had -- ``emit_project`` ran this block inline, and it was the
only part of the emit that owns figure *policy* rather than orchestration.
Moving it also gave the vector-figure reporting below a home; ``project.py``
sits against the repo's 500-line ceiling and had no room for it.

The stage answers three questions in order:

    1. which file wins for each figure (embedded media, a ``figures/fig<N>``
       folder-convention file, or a ``figures.yaml`` entry), and
    2. does the manuscript caption a figure it has no image for, and
    3. -- new -- is each winning file actually vector art, or a screenshot
       that will print soft?

Question 3 is opt-in (``latextify convert --vector-figures``) because it is
advice rather than a defect: a raster figure still compiles, still ships, and
is the right call for a micrograph. It is off by default so an existing
conversion's warning list does not change shape underneath anyone.
"""

from __future__ import annotations

from pathlib import Path

from latextify.emit.figures_copy import _copy_figures, _prune_stale_figures
from latextify.figures.caption_gaps import caption_gaps, gap_warning
from latextify.figures.extract import extract_figures
from latextify.figures.inventory import describe, raster_warnings
from latextify.figures.override import resolve_overrides
from latextify.model.emit import EmitWarning
from latextify.model.figure import Figure


def run_figure_stage(
    docx_path: Path,
    media_dir: Path,
    figures_dir: Path,
    *,
    exclude_figures: bool = False,
    strip_metadata: bool = True,
    vector_figures: bool = False,
    prefix: str = "",
) -> tuple[tuple[Figure, ...], dict[int, str], tuple[EmitWarning, ...]]:
    """Resolve, copy and report this document's figures.

    Args:
        docx_path: the source manuscript; override files are looked for
            beside it (``figures/`` and ``figures.yaml``).
        media_dir: where pandoc extracted the document's embedded media.
        figures_dir: the output tree's ``figures/`` directory to write into.
        exclude_figures: emit no images at all (``--exclude-figures``). Any
            figure this document owns from a previous run is cleared, so
            "exclude" truly ships no images rather than leaving stale files
            in the tree and in any .zip export.
        strip_metadata: remove EXIF/GPS/authoring trails from rasters on the
            way into ``figures/``, which ships as submission source.
        vector_figures: additionally report each figure that is not vector
            art, naming the file to create to replace it.
        prefix: ``""`` for the main document, ``"S"`` for a supplement, so
            the two never collide in the shared ``figures/`` directory.

    Returns ``(figures, figure_files, warnings)`` -- the resolved figure
    records, a map of figure number to the LaTeX-relative path to include,
    and every warning the stage produced.
    """
    if exclude_figures:
        _prune_stale_figures(figures_dir, prefix, set())
        return (), {}, ()

    figures = resolve_overrides(extract_figures(docx_path, media_dir), docx_path, prefix=prefix)

    # A caption with no image behind it silently mislabels this figure and
    # every one after it (pandoc binds a caption to the adjacent image) --
    # report it rather than shipping a wrong figure number.
    warnings = [
        EmitWarning(message=gap_warning(number, prefix))
        for number in caption_gaps(docx_path, len(figures))
    ]

    figure_files, figures, conversion_warnings = _copy_figures(
        figures, figures_dir, prefix=prefix, strip_metadata=strip_metadata
    )
    warnings.extend(conversion_warnings)

    if vector_figures:
        # Describe what was actually WRITTEN, not what was resolved: an SVG
        # source lands here as a PDF, and reporting the source would tell an
        # author to replace a figure that is already vector in what ships. A
        # figure whose conversion wrote nothing (e.g. a TIFF with no readable
        # data) is skipped -- its own conversion warning already names that.
        written = []
        for figure in figures:
            relative = figure_files.get(figure.number)
            if relative is None:
                continue
            path = figures_dir / Path(relative).name
            if path.is_file():
                written.append(describe(figure, path=path))
        warnings.extend(
            EmitWarning(message=message)
            for message in raster_warnings(tuple(written), prefix=prefix)
        )

    return figures, figure_files, tuple(warnings)
