"""Metadata file emission: ``Meta`` IR -> ``generated/metadata.tex``.

The Author/Affiliation -> LaTeX macro mapping itself is data-driven by each
journal's ``metadata.tex.j2`` template and already implemented by
``Journal.render_metadata`` (:mod:`latextify.templates.loader`, plan item 4).
This module's job is the emit-stage orchestration around that: load (or
guess-and-write-once) the manuscript's ``paper.yaml`` sidecar via
:mod:`latextify.ingest.metadata_guess`, then render and write
``generated/metadata.tex``.
"""

from __future__ import annotations

from pathlib import Path

from latextify.ingest.metadata_guess import load_or_create_meta
from latextify.model.meta import Meta
from latextify.templates.loader import Journal


def load_meta(docx_path: Path | str) -> Meta:
    """Load the manuscript's title-page metadata, guessing + writing it once if absent."""
    return load_or_create_meta(docx_path)


def write_metadata_tex(generated_dir: Path | str, meta: Meta, journal: Journal) -> Path:
    """Render ``metadata.tex`` for ``journal`` from ``meta`` and write it.

    Always overwritten -- part of the ``generated/`` regenerate-every-run set
    (see ``latextify/emit/__init__.py``).
    """
    generated_dir = Path(generated_dir)
    generated_dir.mkdir(parents=True, exist_ok=True)
    dest = generated_dir / "metadata.tex"
    dest.write_text(journal.render_metadata(meta), encoding="utf-8")
    return dest
