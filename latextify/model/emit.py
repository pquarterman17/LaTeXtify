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
from latextify.model.validate import ValidationReport


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
class SupplementResult:
    """Outcome of emitting the supplementary-material document (plan item 21).

    Mirrors ``EmitResult``'s write-once/regenerated split for a second,
    S-numbered document sharing the same output tree (same ``figures/``,
    same ``references.bib``): ``supplement_tex_path`` (``supplement.tex``)
    is user-owned and written only once; the ``generated/supplement_*.tex``
    paths are regenerated every run, exactly like the main document's
    ``generated/*.tex``.

    ``figure_count``/``citation_count`` describe the SI document's own
    (S-numbered) figures and in-text citations. ``new_reference_count`` is
    how many of those citations were genuinely new references -- i.e. NOT
    deduplicated against the main document's already-extracted bibliography
    (see :func:`latextify.citations.merge.merge_ref_entries`); a citation
    shared between the main paper and its SI (matched by DOI, source id, or
    author/year/title fingerprint) does not count here since it reuses the
    main document's existing ``references.bib`` entry.
    """

    supplement_tex_path: Path
    supplement_tex_written: bool
    supplement_preamble_tex_path: Path
    supplement_metadata_tex_path: Path
    supplement_body_tex_path: Path
    figure_count: int
    citation_count: int
    new_reference_count: int
    warnings: tuple[EmitWarning, ...] = field(default_factory=tuple)


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
    ``supplement`` is ``None`` unless ``emit_project`` was called with
    ``supplement_docx_path`` set (plan item 21); when present it carries the
    outcome of emitting the second, S-numbered ``supplement.tex`` document.
    ``validation`` is ``None`` unless ``emit_project`` was called with
    ``check_references=True`` (the opt-in online Crossref check); when present
    it carries the per-reference validation outcomes for the report.
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
    supplement: SupplementResult | None = None
    validation: ValidationReport | None = None
