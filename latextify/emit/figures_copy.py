"""Copying this document's figures into the output tree's ``figures/`` directory.

Split out of :mod:`latextify.emit.project` (2026-08-10) so the emit module is
not also the home of every figure-file rule. One document's pass through here
decides, for each figure: what file to write (delegated to
:func:`latextify.figures.convert.convert_for_latex` -- SVG/EPS->PDF,
TIFF->PNG, raster passthrough, and the metadata strip), whether the result is
wide enough to want the journal's two-column float, and which files a previous
run left behind that this one no longer produces.

``prefix`` distinguishes the two documents that share one ``figures/``: the
main document writes ``fig<N>.<ext>``, the supplement ``figS<N>.<ext>``. Every
rule here -- naming, pruning, the ownership regex -- is prefix-scoped so
neither document can touch the other's files or a user's own.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from latextify.figures.convert import convert_for_latex
from latextify.model.emit import EmitWarning
from latextify.model.figure import Figure, FigureSource

#: A figure whose pixel width-to-height ratio meets this threshold is emitted
#: as the journal's wide float (usually ``figure*``) so it spans both columns
#: of a two-column layout instead of being squeezed unreadably into one. 1.3
#: sits between portrait/near-square single-panel plots (kept single-column)
#: and the landscape multi-panel composites that dominate real papers.
#: Deliberately a general ratio, not tuned to any single manuscript (see the
#: generalize-fixes rule).
_WIDE_ASPECT_THRESHOLD = 1.3


def _is_wide_figure(path: Path) -> bool:
    """True when the raster image at ``path`` is landscape past the threshold.

    Measures the copied output file's pixel aspect ratio with Pillow. Any
    failure -- a vector/PDF figure Pillow cannot open, a corrupt file, a zero
    height -- degrades to ``False`` (single-column), never an exception: figure
    *sizing* must not be able to fail a conversion that otherwise compiles.
    """
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        return height > 0 and width / height >= _WIDE_ASPECT_THRESHOLD
    except Exception:  # Pillow's failure modes vary; never crash the emit
        return False


def _copy_figures(
    figures: tuple[Figure, ...],
    figures_dir: Path,
    *,
    prefix: str = "",
    strip_metadata: bool = True,
) -> tuple[dict[int, str], tuple[Figure, ...], tuple[EmitWarning, ...]]:
    """Prepare each figure's resolved file for LaTeX inclusion in ``figures_dir``.

    Delegates the actual copy-vs-convert decision to
    :func:`latextify.figures.convert.convert_for_latex` (SVG->PDF, EPS->PDF
    via Ghostscript or an actionable warning, PDF/PNG/JPG passthrough).
    ``prefix`` (plan item 21) is forwarded to ``convert_for_latex`` so a
    supplementary document's figures land as ``figures/figS<N>.<ext>``
    instead of ``figures/fig<N>.<ext>``, sharing the same output directory
    as the main document's figures without colliding.

    ``strip_metadata`` (METADATA_PRIVACY item 13) is likewise forwarded; on by
    default, so ``figures/`` -- which ships to arXiv/the journal as source --
    carries no camera, GPS or authoring trail.

    Returns a 3-tuple:
        * a map of figure number -> the forward-slashed, LaTeX-relative path
          (``figures/fig<prefix><N><ext>``) to embed in the body;
        * the same figures, each carrying whatever ``conversion_note``
          :func:`convert_for_latex` recorded (``None`` for plain passthrough);
        * any conversion warnings (e.g. EPS with no Ghostscript available),
          to be folded into the overall :class:`EmitResult.warnings`.
    """
    files: dict[int, str] = {}
    updated: list[Figure] = []
    warnings: list[EmitWarning] = []
    # Two figures sharing a number would silently collapse: both copy to the
    # same figures/fig<N>.* path (last write wins) and the number->path map
    # keeps only one. extract_figures numbers sequentially so this shouldn't
    # arise from the normal pipeline, but never drop a figure without a trace.
    counts = Counter(figure.number for figure in figures)
    for number in sorted(n for n, c in counts.items() if c > 1):
        warnings.append(
            EmitWarning(
                message=(
                    f"figure number {number} is used by {counts[number]} figures; only "
                    f"the last is kept as figures/fig{prefix}{number}.* -- check the "
                    "source captions/numbering for a duplicate figure number."
                )
            )
        )
    kept: set[str] = set()
    for figure in figures:
        # The crop (Word's a:srcRect) belongs to the EMBEDDED original only; an
        # override/manifest file is a deliberate replacement authored against no
        # srcRect, so never crop it.
        crop = figure.crop if figure.source is FigureSource.EMBEDDED else None
        outcome = convert_for_latex(
            figure.resolved_path,
            figures_dir,
            figure.number,
            prefix=prefix,
            crop=crop,
            strip_metadata=strip_metadata,
        )
        kept.add(outcome.dest_path.name)
        files[figure.number] = f"figures/{outcome.dest_path.name}"
        if outcome.note is not None:
            figure = replace(figure, conversion_note=outcome.note)
        if not figure.in_table and _is_wide_figure(outcome.dest_path):
            figure = replace(figure, wide=True)
        if outcome.warning is not None:
            warnings.append(EmitWarning(message=f"figure {figure.number}: {outcome.warning}"))
        updated.append(figure)
    # Re-running into an existing tree can leave last run's generated figures
    # behind (fewer figures now, or a format change PNG->PDF). Those stale files
    # would ride along into an exported project/ZIP though nothing references
    # them -- remove the ones this document owns and no longer produced.
    _prune_stale_figures(figures_dir, prefix, kept)
    return files, tuple(updated), tuple(warnings)


# A LaTeXtify-generated figure file: literal "fig" + the document prefix
# ("" main, "S" supplement) + the figure number + an extension. Case-sensitive
# and prefix-scoped so the main pass (``fig<N>.*``) never matches a supplement's
# ``figS<N>.*`` (and vice versa), and a user's own ``Fig1.png``/``diagram.pdf``
# in figures/ is never touched.
def _owned_figure_re(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^fig{re.escape(prefix)}\d+\.")


def _prune_stale_figures(figures_dir: Path, prefix: str, keep_names: set[str]) -> None:
    """Delete this document's generated figures that the current run did not write.

    Only files matching :func:`_owned_figure_re` for ``prefix`` are eligible, so
    user-supplied files and the sibling document's figures are preserved. A run
    with zero figures legitimately clears all of this prefix's generated files.
    """
    if not figures_dir.is_dir():
        return
    owned = _owned_figure_re(prefix)
    for path in figures_dir.iterdir():
        if path.is_file() and path.name not in keep_names and owned.match(path.name):
            path.unlink()


