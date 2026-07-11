"""Figure intermediate representation (plan item 9).

Frozen dataclasses only, no I/O (see ``latextify/model/__init__.py``). A
``Figure`` associates one embedded-media file's document-order figure number
-- matching the body pipeline's ``%%FIGURE:<n>%%`` anchor
(``latextify.ingest.filters.plant_anchors``) -- with the caption text found
adjacent to it, and with whichever file wins override resolution.

Item 3 finding (verified): pandoc can promote a standalone docx image into a
native ``Figure`` AST block, but the block's own ``caption`` is sometimes
empty (derived from alt text rather than the manuscript's real caption); the
real "Figure N: ..." text is left behind as a separate sibling paragraph next
to the anchor. ``latextify.figures.extract`` handles that by falling back to
the sibling paragraph when a Figure block's own caption is empty, so by the
time a ``Figure`` reaches this IR its ``.caption`` is always the best text
found, regardless of which pandoc code path produced it. The emitter (item 5)
still needs to swallow any leftover caption paragraph and empty ``\\caption{}``
shell when it resolves anchors in ``generated/body.tex`` -- that textual
clean-up is out of scope here; this module only produces the association.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class FigureSource(StrEnum):
    """Which override tier the winning figure file came from.

    EMBEDDED -- the media file pandoc extracted from the docx (fallback).
    OVERRIDE -- a ``figures/fig<N>.<ext>`` folder-convention file found
        beside the source docx (plan item 9).
    MANIFEST -- an explicit ``figures.yaml`` mapping entry beside the source
        docx (plan item 15); beats OVERRIDE on conflict for the same number.
    """

    EMBEDDED = "embedded"
    OVERRIDE = "override"
    MANIFEST = "manifest"


@dataclass(frozen=True)
class Figure:
    """One figure: its number, caption, and resolved file provenance.

    Attributes:
        number: 1-based figure number in document order, matching the body
            pipeline's ``%%FIGURE:<number>%%`` anchor.
        caption: caption text associated with the figure (empty string if
            none could be found).
        embedded_path: path to the media file pandoc extracted for this
            figure (``media/imageN.<ext>``).
        override_path: path to a folder-convention or ``figures.yaml``
            manifest override file, if one was found for this figure number
            (see :attr:`source` for which); ``None`` otherwise.
        source: which tier :attr:`resolved_path` came from.
        conversion_note: set at emit time (plan item 15,
            ``latextify.figures.convert``) when :attr:`resolved_path` needed
            conversion for LaTeX inclusion (SVG->PDF, EPS->PDF via
            Ghostscript). ``None`` when the resolved file passed through
            unchanged. Surfaced here -- on the IR, not just as a transient
            emit-time log line -- so the consolidated report (plan item 16)
            can read it straight off the ``Figure`` record.
    """

    number: int
    caption: str
    embedded_path: Path
    override_path: Path | None = None
    source: FigureSource = FigureSource.EMBEDDED
    conversion_note: str | None = None

    @property
    def resolved_path(self) -> Path:
        """The file that should be used for this figure: manifest/override beat embedded."""
        if (
            self.source in (FigureSource.OVERRIDE, FigureSource.MANIFEST)
            and self.override_path is not None
        ):
            return self.override_path
        return self.embedded_path
