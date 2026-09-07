"""One document's whole figure stage: extract, resolve overrides, copy, report.

Split out of :mod:`latextify.emit.project` (2026-09-05) along the seam that
module already had -- ``emit_project`` ran this block inline, and it was the
only part of the emit that owns figure *policy* rather than orchestration.
Moving it also gave the vector-figure reporting below a home; ``project.py``
sits against the repo's 500-line ceiling and had no room for it.

The stage answers four questions in order:

    1. does the manuscript caption a figure it has no image for -- and if a
       replacement was supplied, repair it (see
       :mod:`latextify.figures.gap_fill`);
    2. which file wins for each figure, across every override tier
       (``--figure N=PATH``, a ``--figures-dir`` or split ``--figures-pdf``,
       ``figures.yaml``, the ``figures/`` folder beside the manuscript, then
       the embedded media);
    3. what lands in ``figures/`` (conversion, cropping, the metadata strip);
    4. is each winning file actually vector art, or a screenshot that will
       print soft?

Question 1 runs BEFORE question 2 deliberately -- see the comment at the call
site; resolving first let one supplied file be consumed by two figures.

Question 4 is opt-in (``latextify convert --vector-figures``) because it is
advice rather than a defect: a raster figure still compiles, still ships, and
is the right call for a micrograph. It is off by default so an existing
conversion's warning list does not change shape underneath anyone.
"""

from __future__ import annotations

from pathlib import Path

from latextify.emit.figures_copy import _copy_figures, _prune_stale_figures
from latextify.figures.caption_gaps import caption_gaps, gap_warning
from latextify.figures.extract import extract_figures
from latextify.figures.gap_fill import GapFillPlan, plan_gap_fill
from latextify.figures.inventory import describe, raster_warnings
from latextify.figures.override import OverrideSources, resolve_overrides
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
    sources: OverrideSources | None = None,
) -> tuple[tuple[Figure, ...], dict[int, str], tuple[EmitWarning, ...], GapFillPlan]:
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
        sources: extra places to look for replacement files -- an explicit
            ``--figure N=PATH`` map and any ``--figures-dir`` / split
            ``--figures-pdf`` directories. Empty by default, in which case
            resolution is the manifest and the folder beside the manuscript,
            exactly as before.

    Returns ``(figures, figure_files, warnings, gap_plan)``. The plan says
    whether a captioned-but-missing figure was supplied and therefore whether
    the caller must rewrite the body (see
    :func:`latextify.figures.gap_fill.apply_to_body`); it is inert when nothing
    was filled.
    """
    if exclude_figures:
        _prune_stale_figures(figures_dir, prefix, set())
        return (), {}, (), GapFillPlan(figures=())

    # A caption with no image behind it silently mislabels this figure and
    # every one after it (pandoc binds a caption to the adjacent image). Given
    # a replacement file we repair it outright -- renumbering to the stated
    # captions and planting the missing figure where its orphaned caption sat.
    # Without one, nothing changes and the warning still names the remedy.
    #
    # This runs BEFORE override resolution, and the order is load-bearing.
    # Resolving first meant a file supplied for gap 3 was ALSO picked up by
    # whichever document-order figure happened to be numbered 3 -- the same
    # file emitted twice, under two different figure numbers. Renumbering
    # first means every figure resolves against the number it will actually
    # ship as.
    plan = plan_gap_fill(extract_figures(docx_path, media_dir), docx_path, sources, prefix=prefix)
    figures = resolve_overrides(plan.figures, docx_path, prefix=prefix, sources=sources)
    still_missing = plan.unfilled if plan.changed else caption_gaps(docx_path, len(figures))
    warnings = [EmitWarning(message=gap_warning(number, prefix)) for number in still_missing]
    if plan.filled:
        supplied = ", ".join(str(n) for n in plan.filled)
        warnings.append(
            EmitWarning(
                message=(
                    f"figure(s) {supplied} were captioned but had no image; the supplied "
                    "file(s) were placed at their captions and the remaining figures "
                    "renumbered to match the captions. Check the figure order in the PDF."
                )
            )
        )

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

    return figures, figure_files, tuple(warnings), plan
