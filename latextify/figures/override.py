"""Folder-convention figure override resolution (plan item 9).

Implements tier 2 of the override order documented in
``latextify/figures/__init__.py``: a ``figures/`` directory beside the
source ``.docx`` may contain ``fig<N>.<ext>`` files that should be used
instead of the embedded media for figure ``N``. Tier 1 (an explicit
``figures.yaml`` manifest, which beats folder convention on conflict) is
plan item 15 and is not implemented here -- :class:`~latextify.model.Figure`
only reaches ``FigureSource.EMBEDDED``/``FigureSource.OVERRIDE`` for now.

When more than one override file exists for the same figure number (e.g.
both ``fig2.pdf`` and ``fig2.png``), extension priority picks the winner:
pdf > eps > svg > png > jpg.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from latextify.model import Figure, FigureSource

#: Highest-priority extension first. LaTeX prefers vector formats; PDF is
#: the most broadly embeddable vector format under pdflatex/xelatex/Tectonic.
EXTENSION_PRIORITY: tuple[str, ...] = ("pdf", "eps", "svg", "png", "jpg")


def find_override(figures_dir: Path | str, number: int) -> Path | None:
    """Return the highest-priority ``fig<number>.<ext>`` file in ``figures_dir``.

    Returns ``None`` if ``figures_dir`` doesn't exist or has no matching file
    for ``number`` in any of the recognized extensions.
    """
    figures_dir = Path(figures_dir)
    if not figures_dir.is_dir():
        return None
    for ext in EXTENSION_PRIORITY:
        candidate = figures_dir / f"fig{number}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def resolve_overrides(figures: tuple[Figure, ...], docx_path: Path | str) -> tuple[Figure, ...]:
    """Resolve folder-convention overrides for each figure, beside ``docx_path``.

    Looks for a ``figures/`` directory next to ``docx_path`` (i.e.
    ``docx_path.parent / "figures"``). Figures with a matching override get
    ``override_path`` set and ``source`` flipped to ``FigureSource.OVERRIDE``;
    figures with no match are returned unchanged (still ``EMBEDDED``).
    """
    figures_dir = Path(docx_path).parent / "figures"
    resolved: list[Figure] = []
    for figure in figures:
        override_path = find_override(figures_dir, figure.number)
        if override_path is not None:
            resolved.append(
                replace(figure, override_path=override_path, source=FigureSource.OVERRIDE)
            )
        else:
            resolved.append(figure)
    return tuple(resolved)


def describe_source(figure: Figure) -> str:
    """One-line human-readable record of a figure's file provenance.

    Consumed by the consolidated conversion report (plan item 16); exposed
    here so item 9's override test can assert on it directly.
    """
    return f"Figure {figure.number}: source={figure.source.value} ({figure.resolved_path.name})"
