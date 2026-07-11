"""Figure override resolution: figures.yaml manifest + folder convention (plan items 9, 15).

Implements both non-embedded tiers of the override order documented in
``latextify/figures/__init__.py``:

    1. (plan item 15) an explicit ``figures.yaml`` manifest beside the
       source ``.docx``: ``{<figure-number>: <path>}``. Beats the folder
       convention on conflict -- a number present in the manifest is never
       looked up in ``figures/`` at all.
    2. (plan item 9) a ``figures/`` directory beside the source ``.docx``
       may contain ``fig<N>.<ext>`` files that should be used instead of the
       embedded media for figure ``N``.

When more than one override file exists for the same figure number via the
folder convention (e.g. both ``fig2.pdf`` and ``fig2.png``), extension
priority picks the winner: pdf > eps > svg > png > jpg. The manifest has no
such ambiguity -- each figure number maps to exactly one path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from latextify.model import Figure, FigureSource

#: Highest-priority extension first. LaTeX prefers vector formats; PDF is
#: the most broadly embeddable vector format under pdflatex/xelatex/Tectonic.
EXTENSION_PRIORITY: tuple[str, ...] = ("pdf", "eps", "svg", "png", "jpg")

#: figures.yaml sidecar filename, expected beside the source .docx.
MANIFEST_FILENAME = "figures.yaml"


class FigureManifestError(ValueError):
    """Raised when a figures.yaml manifest fails schema validation.

    The message always names the offending figure number or field (bad
    number, missing file, non-mapping root) so the error is actionable
    without having to open the file -- same style as
    :class:`latextify.ingest.metadata_guess.MetaValidationError`.
    """


def _manifest_number(raw_key: object, source: str) -> int:
    """Validate and coerce one manifest key to a positive figure number."""
    if isinstance(raw_key, int) and not isinstance(raw_key, bool):
        number = raw_key
    elif isinstance(raw_key, str) and raw_key.strip().lstrip("-").isdigit():
        number = int(raw_key.strip())
    else:
        raise FigureManifestError(
            f"{source}: figure number {raw_key!r} must be a positive integer"
        )
    if number < 1:
        raise FigureManifestError(
            f"{source}: figure number {number} must be a positive integer"
        )
    return number


def load_manifest(manifest_path: Path | str) -> dict[int, Path]:
    """Parse and validate a ``figures.yaml`` manifest: ``{<figure-number>: <path>}``.

    Paths are resolved relative to ``manifest_path``'s own directory unless
    already absolute. Every entry is validated eagerly -- bad figure number,
    non-mapping root, missing referenced file -- so a broken manifest fails
    loudly and specifically at resolve time rather than silently falling
    through to the folder/embedded tiers.

    An empty (or ``null``) manifest file is valid and resolves to no entries.
    """
    manifest_path = Path(manifest_path)
    source = manifest_path.name
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FigureManifestError(f"{source}: invalid YAML syntax: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FigureManifestError(
            f"{source}: root must be a mapping, got {type(data).__name__}"
        )

    resolved: dict[int, Path] = {}
    for raw_number, raw_path in data.items():
        number = _manifest_number(raw_number, source)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise FigureManifestError(
                f"{source}: figure {number} path must be a non-empty string, got {raw_path!r}"
            )
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        if not candidate.is_file():
            raise FigureManifestError(
                f"{source}: figure {number} references a file that does not exist: {candidate}"
            )
        resolved[number] = candidate
    return resolved


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
    """Resolve manifest + folder-convention overrides for each figure, beside ``docx_path``.

    Looks for ``figures.yaml`` and a ``figures/`` directory next to
    ``docx_path`` (i.e. ``docx_path.parent``). Resolution order per figure
    number, first match wins:

        1. an entry in ``figures.yaml`` -> ``FigureSource.MANIFEST``
        2. a ``figures/fig<N>.<ext>`` folder-convention file -> ``FigureSource.OVERRIDE``
        3. unchanged (still ``FigureSource.EMBEDDED``)

    A present-but-invalid ``figures.yaml`` raises :class:`FigureManifestError`
    immediately (see :func:`load_manifest`) rather than silently falling
    through to the folder convention -- a broken manifest should never
    resolve as if it were absent.
    """
    docx_path = Path(docx_path)
    figures_dir = docx_path.parent / "figures"
    manifest_path = docx_path.parent / MANIFEST_FILENAME
    manifest_map = load_manifest(manifest_path) if manifest_path.is_file() else {}

    resolved: list[Figure] = []
    for figure in figures:
        manifest_override = manifest_map.get(figure.number)
        if manifest_override is not None:
            resolved.append(
                replace(figure, override_path=manifest_override, source=FigureSource.MANIFEST)
            )
            continue
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
