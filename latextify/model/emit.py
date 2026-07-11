"""Emit-stage IR: anchor-resolution warnings and the overall emit result.

Frozen dataclasses only, per the ``model/`` contract (see
``latextify/model/__init__.py``) -- no I/O, no behavior.
:mod:`latextify.emit.project` resolves ``%%FIGURE:<n>%%``/``%%CITE:<idx>%%``
body anchors and writes the output project tree; this module carries its
result type so callers (the CLI, the consolidated report) consume a typed
object instead of an ad-hoc dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from latextify.model.figure import Figure


@dataclass(frozen=True)
class EmitWarning:
    """One thing the emitter noticed that a human should look at.

    Covers both unresolvable body anchors (no matching Figure/Citation
    record) and softer linkage gaps (e.g. a citation was extracted into
    ``references.bib`` but never got wired into the body via ``\\cite{}``
    because no matching ``%%CITE:<idx>%%`` anchor existed).
    """

    message: str


@dataclass(frozen=True)
class EmitResult:
    """Outcome of one :func:`~latextify.emit.project.emit_project` run.

    Paths always point at files that exist after a successful run.
    ``main_tex_written`` is ``True`` only when ``main.tex`` did not already
    exist and was written for the first time this run (the write-once
    contract); it is ``False`` on every subsequent run against the same
    output directory, even though every other path is regenerated.
    ``figures`` carries the final, post-conversion ``Figure`` records
    (plan item 15's ``convert_for_latex`` fills in ``conversion_note`` on
    each one before this result is built) for the consolidated report
    (plan item 16) to read.
    ``report_path`` is the path to the consolidated report.md (added by item 16).
    """

    output_dir: Path
    journal_name: str
    main_tex_path: Path
    main_tex_written: bool
    preamble_tex_path: Path
    metadata_tex_path: Path
    body_tex_path: Path
    bib_path: Path
    figures_dir: Path
    figure_count: int
    citation_count: int
    figures: tuple[Figure, ...] = field(default_factory=tuple)
    warnings: tuple[EmitWarning, ...] = field(default_factory=tuple)
    report_path: Path | None = None
