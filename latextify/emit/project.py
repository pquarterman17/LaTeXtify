"""Project emitter: write the output LaTeX project tree (plan item 5).

Ties every already-merged stage into one pipeline -- see the module
docstring of :mod:`latextify.emit` for the output tree contract:

    ingest.metadata_guess     -- Meta (title/authors/abstract/keywords)
    ingest.pandoc             -- body LaTeX with ``%%FIGURE:<n>%%`` /
                                  ``%%CITE:<idx>%%`` anchors unresolved
    figures.extract/override  -- resolved Figure IR (manifest/folder/embedded)
    figures.convert           -- SVG/EPS -> PDF for LaTeX inclusion (item 15),
                                  run here at copy time via ``_copy_figures``
    citations.fields/bib      -- Citation/RefEntry IR + a ``.bib`` file body
    templates.loader          -- per-journal preamble/metadata rendering

:func:`emit_project` is the single public entry point.

Anchor resolution (the emitter's own novel logic) handles two shapes pandoc
actually produces for a planted ``%%FIGURE:<n>%%`` marker (verified
empirically against ``figures.docx``, see plan item 9's Completed note for
the caption-finding background):

    1. pandoc promoted the image into its own ``Figure`` AST block, so the
       anchor already sits inside a pandoc-emitted
       ``\\begin{figure}...\\caption{...}...\\end{figure}`` wrapper, and that
       wrapper's own caption duplicates (with the raw "Figure N:" label
       still attached) the already-clean ``Figure.caption`` text.
    2. the anchor is bare (no wrapper at all), and the raw "Figure N: ..."
       caption paragraph pandoc left behind sits immediately after it as a
       separate, now-duplicate paragraph.

Both cases are replaced wholesale with one freshly-built figure environment
using the clean ``Figure.caption`` text, so neither duplicate (empty
``\\caption{}`` shell or leftover caption paragraph) survives into
``generated/body.tex``.

Citation linkage has two paths that both resolve to ``\\cite{...}``:

    * ``ZZLTXCITE<i>ZZ`` sentinels -- the primary path for Zotero/Mendeley
      field codes, planted into the body pre-pandoc by
      ``latextify.ingest.citation_sentinels`` because pandoc 3.9 never emits a
      ``Cite`` node for those field codes. ``<i>`` is 0-based and pairs
      directly with ``Citation.index`` (same shared document-order walk).
    * ``%%CITE:<idx>%%`` anchors -- the legacy path for any genuine ``Cite``
      node ``latextify.ingest.filters.plant_anchors`` sees; 1-based, so anchor
      ``idx`` pairs with ``citations[idx - 1]``. Dormant for field-coded
      documents but kept as it is harmless and future-proof.

When ``extract_field_citations`` finds NO citation fields at all, the emitter
falls back to plain-text reconstruction (plan item 14,
:mod:`latextify.citations.plaintext`): it rebuilds the bibliography from the
typed reference list via Crossref, drops that now-duplicate typed list from the
body, and rewrites the literal in-text markers (``{[}12{]}``,
``\\textsuperscript{...}``, ``(Smith et al., 2020)``) into ``\\cite{...}``.
Unresolvable markers and low-confidence (``verify``) references degrade to
``EmitWarning`` messages, never a crash.
"""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

from latextify.citations.bib import entries_to_bib, escape_latex
from latextify.citations.fields import extract_field_citations
from latextify.citations.plaintext import (
    link_body_markers,
    reconstruct_citations,
    strip_reference_section,
)
from latextify.emit.metadata import load_meta, write_metadata_tex
from latextify.figures.convert import convert_for_latex
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.citation_sentinels import SENTINEL_RE
from latextify.ingest.pandoc import convert_docx_to_body
from latextify.ingest.preflight import run_preflight
from latextify.model.emit import EmitResult, EmitWarning
from latextify.model.figure import Figure
from latextify.model.reconcile import ReconciliationReport
from latextify.model.refs import Citation, RefEntry
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader
from latextify.templates.loader import FigureEnv

_MAIN_TEX_TEMPLATE = (
    "\\input{generated/preamble}\n"
    "\\begin{document}\n"
    "\\input{generated/metadata}\n"
    "\\input{generated/body}\n"
    "\\input{generated/bibliography}\n"
    "\\end{document}\n"
)

# Bibliography inclusion lives in a regenerated file (plan item 26), NOT
# directly in the write-once main.tex, so a citation-free manuscript emits no
# ``\bibliography`` line at all and still compiles under classes whose
# ``\thebibliography`` redefinition errors on an empty reference list
# (IEEEtran: "Something's wrong -- perhaps a missing \item"). When references
# exist the line is written; when they don't, only a self-explaining comment is.
_BIBLIOGRAPHY_LINE = "\\bibliography{references}\n"
_BIBLIOGRAPHY_EMPTY = (
    "% This manuscript has no citations, so no \\bibliography line is emitted.\n"
    "% Regenerated every run: a \\bibliography{references} line reappears here\n"
    "% automatically once citations are found. Emitting an empty \\bibliography\n"
    "% makes some classes -- notably IEEEtran -- error at \\end{thebibliography}.\n"
)
# A pre-item-26 main.tex called ``\bibliography`` directly. main.tex is
# user-owned/write-once so we cannot rewrite it; detect the legacy line (not
# commented out, and distinct from the new ``\input{generated/bibliography}``)
# to advise the one-line migration instead.
_DIRECT_BIBLIOGRAPHY_RE = re.compile(r"(?m)^[^%\n]*\\bibliography\{")

_HYPERREF_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{hyperref\}")
_DEFAULT_HYPERREF_LINE = (
    "\\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}\n"
)

# Case 1: an anchor pandoc already wrapped in its own (possibly caption-
# duplicating) figure environment -- swallow the whole block, caption and all.
_WRAPPED_FIGURE_RE = re.compile(
    r"\\begin\{figure\*?\}.*?%%FIGURE:(?P<num>\d+)%%.*?\\end\{figure\*?\}",
    re.DOTALL,
)
# Case 2: a bare anchor, optionally followed by a leftover "Figure N: ..."/
# "Fig. N: ..." caption paragraph pandoc left as a separate sibling block.
_BARE_FIGURE_RE = re.compile(
    r"%%FIGURE:(?P<num>\d+)%%"
    r"(?:[ \t]*\n[ \t]*\n[ \t]*(?:Figure|Fig\.?)\s*(?P=num)\s*[.:]?.*?(?=\n[ \t]*\n|\Z))?",
    re.IGNORECASE | re.DOTALL,
)
_CITE_RE = re.compile(r"%%CITE:(\d+)%%")


def emit_project(
    docx_path: Path | str,
    journal_name: str,
    output_root: Path | str,
    *,
    citation_style: str | None = None,
    journals_dir: Path | None = None,
    crossref_mailto: str | None = None,
    report: bool = True,
) -> EmitResult:
    """Convert ``docx_path`` into a journal-ready LaTeX project.

    Writes ``output_root/journal_name/`` per the contract documented in
    ``latextify/emit/__init__.py``: ``main.tex`` is written only if absent
    (never overwritten thereafter); ``generated/*.tex``, ``figures/``, and
    ``references.bib`` are rewritten unconditionally every run.

    Args:
        docx_path: source manuscript.
        journal_name: a journal registered under ``templates/journals/``
            (e.g. ``"revtex4-2"``).
        output_root: parent directory of the per-journal output tree; the
            project is written to ``output_root/journal_name/``.
        citation_style: optional citation mode override (``"numeric"`` /
            ``"authoryear"``); defaults to the journal's ``default_mode``.
            Raises :class:`~latextify.templates.loader.ManifestError` if the
            journal doesn't support the requested mode.
        journals_dir: optional override of the journal registry root, for
            testing against a synthetic journal folder.
        crossref_mailto: contact address sent to Crossref during plain-text
            citation reconstruction (only used when the document has no citation
            field codes). Defaults to the ``LATEXTIFY_CROSSREF_MAILTO`` env var
            or a documented placeholder; override it with a real address.
        report: if True (default), generate report.md; if False, skip it.

    Returns:
        An :class:`~latextify.model.emit.EmitResult` naming every written
        path plus any anchor-resolution warnings.
    """
    docx_path = Path(docx_path)
    output_dir = Path(output_root) / journal_name
    generated_dir = output_dir / "generated"
    figures_dir = output_dir / "figures"
    generated_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Preflight: inventory and flag unsupported constructs before any conversion.
    preflight_report = run_preflight(docx_path)

    journal = templates_loader.load(journal_name, journals_dir=journals_dir)
    meta = load_meta(docx_path)

    with tempfile.TemporaryDirectory(prefix="latextify-media-") as tmp:
        media_dir = Path(tmp)
        body_result = convert_docx_to_body(docx_path, media_dir)
        figures = resolve_overrides(extract_figures(docx_path, media_dir), docx_path)
        figure_files, figures, conversion_warnings = _copy_figures(figures, figures_dir)

    citation_result = extract_field_citations(docx_path)

    # pandoc's LaTeX writer emits CRLF on Windows; downstream regexes match
    # literal "\n" boundaries, so normalize before resolving anchors.
    raw_tex = body_result.tex.replace("\r\n", "\n").replace("\r", "\n")
    resolved_tex, anchor_warnings = _resolve_anchors(
        raw_tex, figures, figure_files, citation_result.citations, journal.figure_env
    )
    # body_result.findings (heading clamps, table-normalization degradations --
    # item 25) previously never left convert_docx_to_body's own return value;
    # surfaced here so they reach EmitResult.warnings / the CLI / report.md
    # like every other stage's findings do.
    body_warnings = [EmitWarning(message=finding.message) for finding in body_result.findings]
    warnings = body_warnings + list(conversion_warnings) + list(anchor_warnings)

    reconciliation: ReconciliationReport | None = None
    if citation_result.citations:
        # Field-coded path (Zotero/Mendeley/...): body already carries sentinels
        # /anchors resolved above; keep the extracted, keyed entries verbatim.
        entries: list[RefEntry] = citation_result.entries
        warnings.extend(_citation_linkage_warning(citation_result.citations, resolved_tex))
        citation_count = len(citation_result.citations)
    else:
        # No field codes anywhere -> plain-text reconstruction safety net (item 14).
        entries, resolved_tex, plaintext_warnings, plaintext_records = _link_plaintext_citations(
            docx_path, resolved_tex, crossref_mailto
        )
        warnings.extend(plaintext_warnings)
        citation_count = resolved_tex.count("\\cite{")
        # Capture reconciliation records for the report (item 16).
        if plaintext_records:
            reconciliation = ReconciliationReport(records=plaintext_records)

    bib_text = entries_to_bib(entries)

    preamble_text = _ensure_hyperref(journal.render_preamble(mode=citation_style))
    (generated_dir / "preamble.tex").write_text(preamble_text, encoding="utf-8")

    metadata_tex_path = write_metadata_tex(generated_dir, meta, journal)

    body_tex_path = generated_dir / "body.tex"
    body_tex_path.write_text(resolved_tex, encoding="utf-8")

    bib_path = output_dir / "references.bib"
    bib_path.write_text(bib_text, encoding="utf-8")

    bibliography_tex = _BIBLIOGRAPHY_LINE if bib_text.strip() else _BIBLIOGRAPHY_EMPTY
    (generated_dir / "bibliography.tex").write_text(bibliography_tex, encoding="utf-8")

    main_tex_path = output_dir / "main.tex"
    main_tex_written = not main_tex_path.exists()
    if main_tex_written:
        main_tex_path.write_text(_MAIN_TEX_TEMPLATE, encoding="utf-8")
    else:
        warnings.extend(_legacy_bibliography_warning(main_tex_path))

    # Generate consolidated report (item 16).
    report_path: Path | None = None
    if report:
        report_path = write_report(
            output_dir / "report.md",
            preflight=preflight_report,
            emit_result=None,  # Will be filled below after EmitResult is constructed
            reconciliation=reconciliation,
            compile_result=None,  # Only added if --pdf is used (item 16 CLI wiring)
        )

    result = EmitResult(
        output_dir=output_dir,
        journal_name=journal_name,
        main_tex_path=main_tex_path,
        main_tex_written=main_tex_written,
        preamble_tex_path=generated_dir / "preamble.tex",
        metadata_tex_path=metadata_tex_path,
        body_tex_path=body_tex_path,
        bib_path=bib_path,
        figures_dir=figures_dir,
        figure_count=len(figures),
        citation_count=citation_count,
        figures=figures,
        warnings=tuple(warnings),
        report_path=report_path,
    )

    # Rewrite report with emit_result included (now that we have the full result).
    if report:
        report_path = write_report(
            output_dir / "report.md",
            preflight=preflight_report,
            emit_result=result,
            reconciliation=reconciliation,
            compile_result=None,
        )

    return result


# --------------------------------------------------------------------------- #
# Figure file copying + vector conversion (plan items 5, 15)
# --------------------------------------------------------------------------- #


def _copy_figures(
    figures: tuple[Figure, ...], figures_dir: Path
) -> tuple[dict[int, str], tuple[Figure, ...], tuple[EmitWarning, ...]]:
    """Prepare each figure's resolved file for LaTeX inclusion in ``figures_dir``.

    Delegates the actual copy-vs-convert decision to
    :func:`latextify.figures.convert.convert_for_latex` (SVG->PDF, EPS->PDF
    via Ghostscript or an actionable warning, PDF/PNG/JPG passthrough).

    Returns a 3-tuple:
        * a map of figure number -> the forward-slashed, LaTeX-relative path
          (``figures/fig<N><ext>``) to embed in the body;
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
                    f"the last is kept as figures/fig{number}.* -- check the source "
                    "captions/numbering for a duplicate figure number."
                )
            )
        )
    for figure in figures:
        outcome = convert_for_latex(figure.resolved_path, figures_dir, figure.number)
        files[figure.number] = f"figures/{outcome.dest_path.name}"
        if outcome.note is not None:
            figure = replace(figure, conversion_note=outcome.note)
        if outcome.warning is not None:
            warnings.append(EmitWarning(message=f"figure {figure.number}: {outcome.warning}"))
        updated.append(figure)
    return files, tuple(updated), tuple(warnings)


# --------------------------------------------------------------------------- #
# Preamble: hyperref wiring for clickable DOIs
# --------------------------------------------------------------------------- #


def _ensure_hyperref(preamble_text: str) -> str:
    """Append hyperref wiring if the journal's own preamble doesn't already load it."""
    if _HYPERREF_RE.search(preamble_text):
        return preamble_text
    if not preamble_text.endswith("\n"):
        preamble_text += "\n"
    return preamble_text + _DEFAULT_HYPERREF_LINE


# --------------------------------------------------------------------------- #
# Backward-compat: pre-item-26 main.tex with a direct \bibliography call
# --------------------------------------------------------------------------- #


def _legacy_bibliography_warning(main_tex_path: Path) -> list[EmitWarning]:
    """Advise migrating a pre-item-26 main.tex off its direct ``\\bibliography`` call.

    New projects ``\\input{generated/bibliography}`` so a citation-free
    manuscript emits no ``\\bibliography`` line and still compiles under
    IEEEtran (plan item 26). A ``main.tex`` written before that change is
    user-owned and write-once -- it still carries the direct
    ``\\bibliography{references}`` line, which breaks citation-free IEEEtran
    compiles -- so surface a one-line-edit warning rather than silently
    leaving it broken. Returns no warning once the file has been migrated (it
    then contains the ``\\input{generated/bibliography}`` include).
    """
    try:
        existing = main_tex_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if "\\input{generated/bibliography}" in existing:
        return []
    if _DIRECT_BIBLIOGRAPHY_RE.search(existing):
        return [
            EmitWarning(
                message=(
                    "main.tex calls \\bibliography{references} directly; new projects "
                    "\\input{generated/bibliography} instead so citation-free manuscripts "
                    "compile (an empty \\bibliography breaks IEEEtran). Replace the "
                    "\\bibliography{references} line in main.tex with "
                    "\\input{generated/bibliography}."
                )
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# Anchor resolution
# --------------------------------------------------------------------------- #


def _figure_block(path: str, caption: str, env: str) -> str:
    caption_line = f"\\caption{{{escape_latex(caption)}}}\n" if caption else ""
    return (
        f"\\begin{{{env}}}\n"
        f"\\centering\n"
        f"\\includegraphics{{{path}}}\n"
        f"{caption_line}"
        f"\\end{{{env}}}"
    )


def _resolve_one_figure(
    number: int,
    figures_by_number: dict[int, Figure],
    figure_files: dict[int, str],
    figure_env: FigureEnv,
    warnings: list[EmitWarning],
) -> str:
    figure = figures_by_number.get(number)
    path = figure_files.get(number)
    if figure is None or path is None:
        warnings.append(
            EmitWarning(
                message=f"unresolved figure anchor for figure {number}: no matching Figure record"
            )
        )
        return (
            f"% LATEXTIFY WARNING: unresolved anchor for figure {number}\n"
            f"\\textbf{{[UNRESOLVED FIGURE {number}]}}"
        )
    return _figure_block(path, figure.caption, figure_env.single)


def _resolve_figure_anchors(
    tex: str,
    figures_by_number: dict[int, Figure],
    figure_files: dict[int, str],
    figure_env: FigureEnv,
) -> tuple[str, list[EmitWarning]]:
    warnings: list[EmitWarning] = []

    def replace(match: re.Match[str]) -> str:
        return _resolve_one_figure(
            int(match.group("num")), figures_by_number, figure_files, figure_env, warnings
        )

    # Case 1 (wrapped) first, so a pandoc-emitted figure wrapper's own
    # duplicate caption never survives into the case-2 bare-anchor pass.
    tex = _WRAPPED_FIGURE_RE.sub(replace, tex)
    tex = _BARE_FIGURE_RE.sub(replace, tex)
    return tex, warnings


def _resolve_citation_anchors(
    tex: str, citations: tuple[Citation, ...]
) -> tuple[str, list[EmitWarning]]:
    warnings: list[EmitWarning] = []
    # Anchors are 1-based (plant_anchors); Citation.index is 0-based (document order).
    by_position = {citation.index + 1: citation for citation in citations}

    def replace(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return _cite_command(by_position.get(idx), warnings, "anchor", str(idx))

    return _CITE_RE.sub(replace, tex), warnings


def _cite_command(citation: Citation, warnings: list[EmitWarning], what: str, ref: str) -> str:
    """Render one Citation to ``\\cite{...}`` or a warning+placeholder."""
    if citation is None or not citation.keys:
        warnings.append(
            EmitWarning(message=f"unresolved citation {what} {ref}: no matching citation record")
        )
        return (
            f"% LATEXTIFY WARNING: unresolved citation {what} {ref}\n"
            f"\\textbf{{[UNRESOLVED CITATION]}}"
        )
    return f"\\cite{{{','.join(citation.keys)}}}"


def _resolve_citation_sentinels(
    tex: str, citations: tuple[Citation, ...]
) -> tuple[str, list[EmitWarning]]:
    """Swap ``ZZLTXCITE<i>ZZ`` sentinels for ``\\cite{...}``.

    Sentinel index ``i`` is 0-based and pairs directly with ``Citation.index``
    (both come from the shared document-order field walk). A sentinel with no
    matching citation degrades to a LaTeX comment + warning, never a crash.
    """
    warnings: list[EmitWarning] = []
    by_index = {citation.index: citation for citation in citations}

    def replace(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return _cite_command(by_index.get(idx), warnings, "sentinel", str(idx))

    return SENTINEL_RE.sub(replace, tex), warnings


def _resolve_anchors(
    tex: str,
    figures: tuple[Figure, ...],
    figure_files: dict[int, str],
    citations: tuple[Citation, ...],
    figure_env: FigureEnv,
) -> tuple[str, tuple[EmitWarning, ...]]:
    figures_by_number = {figure.number: figure for figure in figures}
    tex, figure_warnings = _resolve_figure_anchors(tex, figures_by_number, figure_files, figure_env)
    tex, citation_warnings = _resolve_citation_anchors(tex, citations)
    tex, sentinel_warnings = _resolve_citation_sentinels(tex, citations)
    return tex, tuple(figure_warnings + citation_warnings + sentinel_warnings)


def _citation_linkage_warning(
    citations: tuple[Citation, ...], resolved_tex: str
) -> tuple[EmitWarning, ...]:
    """Flag citations that were extracted but never linked into the body.

    Not part of the plan's literal "unresolvable anchor" case (that's an
    anchor with no matching Citation); this is the inverse and softer gap --
    a Citation with no matching anchor at all, which happens when the
    upstream pandoc body pipeline didn't recognize a citation source's field
    codes as a native ``Cite`` element and so never planted a ``%%CITE%%``
    anchor for it in the first place. ``references.bib`` is unaffected --
    every extracted reference is written regardless -- but the body loses
    the inline ``\\cite{}`` link, which is worth surfacing.
    """
    if not citations:
        return ()
    linked = resolved_tex.count("\\cite{")
    if linked >= len(citations):
        return ()
    return (
        EmitWarning(
            message=(
                f"{len(citations)} citation(s) extracted from field codes but only "
                f"{linked} linked into the body via \\cite{{}} -- the rest had no "
                "matching %%CITE%% anchor in the converted body (references.bib still "
                "contains every entry; only the inline link is missing)."
            )
        ),
    )


# --------------------------------------------------------------------------- #
# Plain-text citation reconstruction (item 14) -- the no-field-codes fallback
# --------------------------------------------------------------------------- #


def _link_plaintext_citations(
    docx_path: Path, tex: str, mailto: str | None
) -> tuple[list[RefEntry], str, list[EmitWarning], tuple]:
    """Reconstruct a typed bibliography and link its in-text markers.

    Returns the reconstructed ``.bib`` entries, the body with markers rewritten
    to ``\\cite{...}`` and the duplicate typed reference list removed, the
    accumulated warnings (unresolved markers + low-confidence ``verify`` refs),
    and the reconciliation records for the report.
    A document with no typed reference list yields no entries and an untouched
    body -- there is nothing to reconstruct or link.
    """
    result = reconstruct_citations(docx_path, mailto=mailto)
    if not result.has_reference_list:
        return [], tex, [], ()
    tex = strip_reference_section(tex, result)
    tex, messages = link_body_markers(tex, result)
    warnings = [EmitWarning(message=message) for message in messages]
    warnings.extend(_verify_warnings(result.records))
    return result.entries, tex, warnings, result.records


def _verify_warnings(records) -> list[EmitWarning]:
    """One loud warning per below-threshold (``verify``) reconstructed reference."""
    warnings: list[EmitWarning] = []
    for record in records:
        if not record.verify:
            continue
        number = f" [{record.ref_number}]" if record.ref_number is not None else ""
        warnings.append(
            EmitWarning(
                message=(
                    f"reference{number} could not be confidently matched to Crossref "
                    f"(best score {record.score:.2f}); emitted from raw text -- verify "
                    f"the references.bib entry '{record.key}'."
                )
            )
        )
    return warnings
