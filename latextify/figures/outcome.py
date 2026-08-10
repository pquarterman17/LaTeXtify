"""What one figure's trip through the converters produced.

Its own module (2026-08-10) for a structural reason: every per-format
converter returns a :class:`ConversionOutcome`, so while this dataclass lived
in ``convert.py`` none of them could leave it -- importing it back would have
been circular. Pulling the type out is what let the SVG/EPS and raster
converters move into :mod:`latextify.figures.vector` and
:mod:`latextify.figures.raster`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConversionOutcome:
    """Result of preparing one figure file for LaTeX inclusion.

    ``dest_path`` is always the file actually written into the output
    tree's ``figures/`` directory -- for passthrough formats this is a copy
    of the source; for a converted format it is the new PDF; for a failed
    conversion it is a last-resort copy of the original (so the compile
    fails with a normal "file not found for that format" error rather than
    a missing-file error, and the reason is in ``warning``).

    ``note`` is a short, human-readable description of a conversion that
    *succeeded* but is worth recording (e.g. which converter ran, fidelity
    caveats) -- meant to flow onto the ``Figure`` IR's ``conversion_note``
    field and, eventually, the item 16 consolidated report. ``None`` when
    nothing noteworthy happened (plain passthrough).

    ``warning`` is set instead of ``note`` when conversion could not happen
    at all; the caller surfaces it as an
    :class:`~latextify.model.emit.EmitWarning`. Never set together with
    ``note``.
    """

    dest_path: Path
    note: str | None = None
    warning: str | None = None
