"""Intermediate representation for the pandoc body-conversion stage (item 3).

The body pipeline (``latextify.ingest.pandoc.convert_docx_to_body``) does not
build a structured ``Document``/``Section`` tree: pandoc's own LaTeX writer
already serializes the body to text, and re-parsing that back into a tree
would throw away information for no benefit downstream. Instead the pipeline
returns a :class:`BodyConversionResult`: the emitted LaTeX fragment (with
``%%FIGURE:<n>%%`` / ``%%CITE:<idx>%%`` anchors still unresolved), the media
directory pandoc extracted embedded images into, how many of each anchor kind
were planted (so the figures/citations stages know how many they must
resolve), and any :class:`FilterFinding` notes raised while normalizing the
AST (e.g. a heading deeper than ``\\subsubsection`` got clamped).

The emitter (item 5) consumes ``.tex`` directly when writing
``generated/body.tex`` and resolves the anchors using the Figure/Citation IR
from the figures/citations stages. The report (item 16) consumes
``.findings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FilterFinding:
    """One thing a panflute AST filter noticed while normalizing the body."""

    message: str


@dataclass(frozen=True)
class BodyConversionResult:
    """Output of the docx -> pandoc AST -> LaTeX body pipeline.

    Attributes:
        tex: the emitted LaTeX body fragment, with ``%%FIGURE:<n>%%`` and
            ``%%CITE:<idx>%%`` raw-LaTeX anchors still unresolved.
        media_dir: directory pandoc's ``--extract-media`` wrote embedded
            images into (``media/imageN.<ext>``, in document order).
        figure_count: number of ``%%FIGURE:<n>%%`` anchors planted.
        citation_count: number of ``%%CITE:<idx>%%`` anchors planted.
        findings: notes from the normalization filters (e.g. clamped
            heading levels), for the consolidated report.
    """

    tex: str
    media_dir: Path
    figure_count: int
    citation_count: int
    findings: tuple[FilterFinding, ...] = field(default_factory=tuple)
