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

A third case -- an anchor whose ``Figure`` record has ``in_table=True``
(``latextify.figures.extract`` set this because the source ``Image`` sat
inside a table cell) -- always takes the case-2 (bare anchor) shape, since
``latextify.ingest.filters.normalize_tables`` flattens the cell to plain
LaTeX text before an anchor there could ever end up pandoc-wrapped in a
``\\begin{figure}...\\end{figure}`` block. It resolves to a bare, width-
limited ``\\includegraphics`` with no float wrapper and no ``\\caption``:
``\\begin{figure}`` is not legal LaTeX inside a ``tabular``/``longtable``
cell.

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

Supplementary material (plan item 21, ``supplement_docx_path``): a second
manuscript runs through this exact same pipeline (preflight, pandoc body,
figures, citations) into the SAME output tree as a second write-once
document, ``supplement.tex`` + ``generated/supplement_*.tex`` -- see
``_emit_supplement``. Its figures share ``figures/`` with the main document
under an ``S`` prefix (``figS<N>.<ext>``, via ``prefix="S"`` threaded
through ``figures.override``/``figures.convert``); its citations are merged
into the shared ``references.bib`` by
:func:`latextify.citations.merge.merge_ref_entries`, which reuses
``citations.fields.dedup_identity`` so a reference cited in both documents
(matched by DOI, source id, or author/year/title fingerprint) collapses to
one entry. Omitting ``supplement_docx_path`` leaves the main document's
output byte-identical to before item 21.
"""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

from latextify.citations.bib import entries_to_bib, escape_latex
from latextify.citations.crossref import CrossrefClient
from latextify.citations.fields import extract_field_citations
from latextify.citations.merge import merge_ref_entries
from latextify.citations.plaintext import (
    link_body_markers,
    reconstruct_citations,
    strip_reference_section,
    strip_reference_section_to_eof,
)
from latextify.citations.refs_import import parse_references_file
from latextify.citations.validate import validate_references
from latextify.emit.anchors import (
    citation_linkage_warning,
    remap_cite_keys_in_text,
    resolve_anchors,
)
from latextify.emit.metadata import load_meta, write_metadata_tex
from latextify.emit.submission import (
    _ONECOLUMN_FIGURE_ENV,
    DocumentLayout,
    anonymize_meta,
    build_main_preamble,
    build_supplement_preamble,
    strip_acknowledgments,
)
from latextify.figures.convert import convert_for_latex
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.formats import non_docx_warnings
from latextify.ingest.metadata_guess import sidecar_path_for
from latextify.ingest.pandoc import convert_docx_to_body
from latextify.ingest.preflight import run_preflight
from latextify.model.emit import EmitResult, EmitWarning, SupplementResult
from latextify.model.figure import Figure, FigureSource
from latextify.model.meta import Meta
from latextify.model.reconcile import ReconciliationReport
from latextify.model.refs import Citation, RefEntry
from latextify.model.validate import ValidationReport
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader
from latextify.templates.loader import Journal

_MAIN_TEX_TEMPLATE = (
    "\\input{generated/preamble}\n"
    "\\begin{document}\n"
    "\\input{generated/metadata}\n"
    "\\input{generated/body}\n"
    "\\input{generated/bibliography}\n"
    "\\end{document}\n"
)

# Supplementary material (plan item 21): a second write-once document, the
# same shape as main.tex, \input-ing its own regenerated generated/
# supplement_*.tex set. It shares this project's figures/ and
# references.bib with the main document.
_SUPPLEMENT_TEX_TEMPLATE = (
    "\\input{generated/supplement_preamble}\n"
    "\\begin{document}\n"
    "\\input{generated/supplement_metadata}\n"
    "\\input{generated/supplement_body}\n"
    "\\input{generated/supplement_bibliography}\n"
    "\\end{document}\n"
)

# Appended to the SI's own rendered preamble (plan item 21): S1, S2, ...
# numbering for figures/tables/equations/sections, the conventional SI
# numbering scheme. LaTeX's own \arabic{<counter>} does the counting -- each
# \begin{figure}/\begin{table}/equation/\section in supplement.tex increments
# its own counter starting at 1, independent of the main document's (a
# separate top-level LaTeX document = separate counters), so no other
# bookkeeping is needed to get "S1", "S2", ... into the compiled output.
_SUPPLEMENT_NUMBERING = (
    "\n% Supplementary numbering (plan item 21).\n"
    "\\renewcommand{\\thefigure}{S\\arabic{figure}}\n"
    "\\renewcommand{\\thetable}{S\\arabic{table}}\n"
    "\\renewcommand{\\theequation}{S\\arabic{equation}}\n"
    "\\renewcommand{\\thesection}{S\\arabic{section}}\n"
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

def emit_project(
    docx_path: Path | str,
    journal_name: str,
    output_root: Path | str,
    *,
    citation_style: str | None = None,
    journals_dir: Path | None = None,
    crossref_mailto: str | None = None,
    report: bool = True,
    exclude_figures: bool = False,
    supplement_docx_path: Path | str | None = None,
    references_bib_path: Path | str | None = None,
    supplement_onecolumn: bool = False,
    check_references: bool = False,
    main_layout: DocumentLayout | None = None,
    supplement_layout: DocumentLayout | None = None,
    anonymize: bool = False,
    figures_at_end: bool = False,
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
        exclude_figures: when True, emit a text-only project -- every figure
            float is dropped (no ``\\includegraphics``, no leftover caption)
            and no image is copied into ``figures/``. Citations, tables, and
            equations are unaffected. Applied to the supplement too, so a
            two-document conversion stays consistently text-only. Defaults to
            ``False`` (figures included).
        check_references: when True, every assembled reference is validated
            online against Crossref (opt-in; needs a network connection). A
            reference with a DOI is resolved and its stored fields compared
            against the canonical record; one without a DOI is searched so a DOI
            can be suggested. Results are attached to ``EmitResult.validation``
            and summarized in report.md. Degrades gracefully -- a Crossref
            outage marks references ``unchecked`` rather than failing the emit.
            Defaults to ``False`` (no network).
        supplement_onecolumn: when True (and a supplement is given), the
            Supplementary Information is emitted as a simplified one-column
            ``\\documentclass[11pt]{article}`` instead of the journal's class,
            keeping the shared references/figures and S-numbering. Ignored when
            no supplement is given. The many journals with looser SI formatting
            rules accept this.
        references_bib_path: optional ``.bib`` export of the author's reference
            manager. Used only on the plain-text citation path (a document with
            no field codes): each typed reference is matched against these
            entries first -- authoritative and offline -- and only references
            the ``.bib`` doesn't cover fall through to Crossref (see
            :mod:`latextify.citations.bibmatch`). A reference list fully covered
            by the ``.bib`` therefore needs no network. Shared with the
            supplement. ``None`` (default) preserves the Crossref-only behavior.
        supplement_docx_path: optional second manuscript (Supplementary
            Information) to emit alongside the main document into the SAME
            output tree, as a write-once ``supplement.tex`` +
            ``generated/supplement_*.tex`` (plan item 21). Runs through the
            same preflight/pandoc/figures/citations pipeline as the main
            document; its figures land in the shared ``figures/`` directory
            as S-numbered ``figS<N>.<ext>``, and its citations are merged
            into the shared ``references.bib`` (deduped by DOI/source id/
            fingerprint against the main document's references -- see
            :func:`latextify.citations.merge.merge_ref_entries`). No
            metadata guessing runs on this document; its title block is
            derived from the main document's ``paper.yaml`` alone. ``None``
            (default) leaves the main document's output byte-identical to
            not passing this argument at all.

        main_layout / supplement_layout: optional per-document
            :class:`~latextify.emit.submission.DocumentLayout` overrides
            (column mode, reviewer line numbers, double spacing) applied to
            the rendered preambles. A supplement layout with ``columns="one"``
            selects the plain-article supplement exactly like
            ``supplement_onecolumn``. ``None`` keeps the journal defaults.
        anonymize: double-blind submission -- render a placeholder author
            block with no affiliations and strip the acknowledgments
            section/environment from the body (noted in report warnings).
        figures_at_end: gather figure/table floats after the references via
            the ``endfloat`` package, as several publishers require at
            submission. Applies to both emitted documents.

    Returns:
        An :class:`~latextify.model.emit.EmitResult` naming every written
        path plus any anchor-resolution warnings. ``.supplement`` is
        ``None`` unless ``supplement_docx_path`` was given.
    """
    docx_path = Path(docx_path)
    output_dir = Path(output_root) / journal_name
    generated_dir = output_dir / "generated"
    figures_dir = output_dir / "figures"
    generated_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Parse the author's .bib once (if given); shared by the main document and
    # the supplement's plain-text citation paths. Field-coded documents ignore
    # it -- they already carry full metadata in their citation field codes.
    bib_entries: list[RefEntry] | None = None
    if references_bib_path is not None:
        bib_entries = parse_references_file(references_bib_path)

    # Preflight: inventory and flag unsupported constructs before any conversion.
    preflight_report = run_preflight(docx_path)

    journal = templates_loader.load(journal_name, journals_dir=journals_dir)
    sidecar_existed = sidecar_path_for(docx_path).exists()  # before load_meta may write it
    meta = load_meta(docx_path)
    if anonymize:
        # After load_meta so the (write-once) paper.yaml sidecar keeps the real
        # author block; only this run's rendered output is anonymized. The
        # report warning is added once `warnings` exists, with the body strip.
        meta = anonymize_meta(meta)

    with tempfile.TemporaryDirectory(prefix="latextify-media-") as tmp:
        media_dir = Path(tmp)
        # strip_front_matter: the manuscript's own title page is re-rendered
        # by the journal metadata template, so remove it from the body to
        # avoid it appearing twice in the PDF (gap 4).
        body_result = convert_docx_to_body(docx_path, media_dir, strip_front_matter=True)
        if exclude_figures:
            # Text-only emit (--exclude-figures): skip figure extraction and
            # copy entirely; the anchors left in the body are stripped below.
            figures: tuple[Figure, ...] = ()
            figure_files: dict[int, str] = {}
            conversion_warnings: tuple[EmitWarning, ...] = ()
            # Re-running with figures now excluded into an existing tree would
            # otherwise leave a prior run's images behind (and in any .zip
            # export) -- clear this document's owned figures so "exclude" truly
            # ships no images.
            _prune_stale_figures(figures_dir, "", set())
        else:
            figures = resolve_overrides(extract_figures(docx_path, media_dir), docx_path)
            figure_files, figures, conversion_warnings = _copy_figures(figures, figures_dir)

    citation_result = extract_field_citations(docx_path)

    # pandoc's LaTeX writer emits CRLF on Windows; downstream regexes match
    # literal "\n" boundaries, so normalize before resolving anchors.
    raw_tex = body_result.tex.replace("\r\n", "\n").replace("\r", "\n")
    resolved_tex, anchor_warnings = resolve_anchors(
        raw_tex,
        figures,
        figure_files,
        citation_result.citations,
        journal.figure_env,
        exclude_figures=exclude_figures,
    )
    # body_result.findings (heading clamps, table-normalization degradations --
    # item 25) previously never left convert_docx_to_body's own return value;
    # surfaced here so they reach EmitResult.warnings / the CLI / report.md
    # like every other stage's findings do.
    body_warnings = [EmitWarning(message=finding.message) for finding in body_result.findings]
    warnings = body_warnings + list(conversion_warnings) + list(anchor_warnings)
    warnings.extend(EmitWarning(message=m) for m in non_docx_warnings(docx_path, sidecar_existed))

    reconciliation: ReconciliationReport | None = None
    if citation_result.citations:
        # Field-coded path (Zotero/Mendeley/...): body already carries sentinels
        # /anchors resolved above; keep the extracted, keyed entries verbatim.
        entries: list[RefEntry] = citation_result.entries
        warnings.extend(citation_linkage_warning(citation_result.citations, resolved_tex))
        citation_count = len(citation_result.citations)
        # A reference manager's Word plugin often drops a FORMATTED bibliography
        # into the document too. The project renders its own \bibliography from
        # references.bib, so that leftover list is a duplicate -- strip it (to
        # EOF from its heading), same as the plaintext path strips its typed
        # list. Unchanged when the document carries no such section.
        stripped_tex = strip_reference_section_to_eof(resolved_tex)
        if stripped_tex != resolved_tex:
            resolved_tex = stripped_tex
            warnings.append(
                EmitWarning(
                    message=(
                        "removed the reference manager's formatted bibliography from the "
                        "body -- the reference list is rendered from references.bib via "
                        "\\bibliography instead (avoids a duplicate list)."
                    )
                )
            )
    else:
        # No field codes anywhere -> plain-text reconstruction safety net (item 14).
        entries, resolved_tex, plaintext_warnings, plaintext_records = _link_plaintext_citations(
            docx_path, resolved_tex, crossref_mailto, bib_entries
        )
        warnings.extend(plaintext_warnings)
        citation_count = resolved_tex.count("\\cite{")
        # Capture reconciliation records for the report (item 16).
        if plaintext_records:
            reconciliation = ReconciliationReport(records=plaintext_records)

    bib_text = entries_to_bib(entries)

    if anonymize:
        resolved_tex, ack_removed = strip_acknowledgments(resolved_tex)
        note = "anonymize: placeholder author block, affiliations removed"
        if ack_removed:
            note += "; acknowledgments section removed"
        warnings.append(EmitWarning(message=note + " (double-blind review)."))

    preamble_text = build_main_preamble(
        journal.render_preamble(mode=citation_style),
        document_class=journal.document_class,
        layout=main_layout,
        figures_at_end=figures_at_end,
    )
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

    # Supplementary material (plan item 21): a second write-once document
    # sharing this project's figures/ and references.bib. Emitted before the
    # EmitResult/report are built so the (possibly bib-merging) outcome
    # folds into one result object and one final report write.
    supplement_result: SupplementResult | None = None
    if supplement_docx_path is not None:
        supplement_result, entries = _emit_supplement(
            Path(supplement_docx_path),
            output_dir=output_dir,
            generated_dir=generated_dir,
            figures_dir=figures_dir,
            journal=journal,
            main_meta=meta,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
            main_entries=entries,
            bib_entries=bib_entries,
            onecolumn=supplement_onecolumn
            or (supplement_layout is not None and supplement_layout.columns == "one"),
            exclude_figures=exclude_figures,
            layout=supplement_layout,
            figures_at_end=figures_at_end,
        )
        # references.bib is shared by main.tex and supplement.tex; rewrite it
        # with the merged set now that any new SI-only references were
        # folded in (main entries keep their already-resolved keys
        # unchanged, so main's body.tex, written above, stays correct).
        bib_path.write_text(entries_to_bib(entries), encoding="utf-8")

    # Online reference validation (opt-in): runs on the FINAL merged entry set
    # (after any supplement folded its references in), so every reference in the
    # shared references.bib -- main and SI alike -- is checked exactly once.
    validation: ValidationReport | None = None
    if check_references and entries:
        validation, validation_warnings = _run_reference_validation(entries, crossref_mailto)
        warnings.extend(validation_warnings)

    # The report path is deterministic, so we don't need to write anything to
    # know it; the single write happens below, after the EmitResult exists, so
    # it can include emit_result (figures/warnings) in one pass rather than
    # writing a placeholder report first and overwriting it (item 16; item 21
    # adds the Supplement section).
    report_path: Path | None = (output_dir / "report.md") if report else None

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
        supplement=supplement_result,
        validation=validation,
        entries=tuple(entries),
    )

    # Write the report once, now that the full EmitResult is available.
    if report:
        write_report(
            output_dir / "report.md",
            preflight=preflight_report,
            emit_result=result,
            reconciliation=reconciliation,
            compile_result=None,
            supplement=supplement_result,
            validation=validation,
        )

    return result


# --------------------------------------------------------------------------- #
# Figure file copying + vector conversion (plan items 5, 15)
# --------------------------------------------------------------------------- #


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
    figures: tuple[Figure, ...], figures_dir: Path, *, prefix: str = ""
) -> tuple[dict[int, str], tuple[Figure, ...], tuple[EmitWarning, ...]]:
    """Prepare each figure's resolved file for LaTeX inclusion in ``figures_dir``.

    Delegates the actual copy-vs-convert decision to
    :func:`latextify.figures.convert.convert_for_latex` (SVG->PDF, EPS->PDF
    via Ghostscript or an actionable warning, PDF/PNG/JPG passthrough).
    ``prefix`` (plan item 21) is forwarded to ``convert_for_latex`` so a
    supplementary document's figures land as ``figures/figS<N>.<ext>``
    instead of ``figures/fig<N>.<ext>``, sharing the same output directory
    as the main document's figures without colliding.

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
            figure.resolved_path, figures_dir, figure.number, prefix=prefix, crop=crop
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
# Plain-text citation reconstruction (item 14) -- the no-field-codes fallback
# --------------------------------------------------------------------------- #


def _link_plaintext_citations(
    docx_path: Path, tex: str, mailto: str | None, bib_entries: list[RefEntry] | None = None
) -> tuple[list[RefEntry], str, list[EmitWarning], tuple]:
    """Reconstruct a typed bibliography and link its in-text markers.

    Returns the reconstructed ``.bib`` entries, the body with markers rewritten
    to ``\\cite{...}`` and the duplicate typed reference list removed, the
    accumulated warnings (unresolved markers + low-confidence ``verify`` refs),
    and the reconciliation records for the report.
    A document with no typed reference list yields no entries and an untouched
    body -- there is nothing to reconstruct or link. ``bib_entries`` (the
    author's parsed ``.bib``) is matched before Crossref when supplied.
    """
    result = reconstruct_citations(docx_path, mailto=mailto, bib_entries=bib_entries)
    if not result.has_reference_list:
        return [], tex, [], ()
    tex = strip_reference_section(tex, result)
    tex, messages = link_body_markers(tex, result)
    warnings = [EmitWarning(message=message) for message in messages]
    warnings.extend(_verify_warnings(result.records))
    return result.entries, tex, warnings, result.records


def _run_reference_validation(
    entries: list[RefEntry], mailto: str | None
) -> tuple[ValidationReport | None, list[EmitWarning]]:
    """Validate the assembled bibliography online (opt-in ``--check-references``).

    Opens a single Crossref client, validates every entry serially, and returns
    the report plus any user-facing warnings. Never propagates a failure: a
    fully offline run yields an all-``unchecked`` report (with one advisory
    warning), and any unexpected error degrades to ``None`` + a warning rather
    than failing an otherwise-successful emit -- reference checking is a bonus
    pass, never a gate.
    """
    try:
        with CrossrefClient(mailto=mailto) as client:
            report = validate_references(entries, client)
    except Exception as exc:  # never let a bonus check sink the whole emit
        return None, [
            EmitWarning(
                message=(
                    "online reference check could not run "
                    f"({type(exc).__name__}: {exc}); skipped. References were not verified."
                )
            )
        ]
    warnings: list[EmitWarning] = []
    if not report.any_checked:
        warnings.append(
            EmitWarning(
                message=(
                    "online reference check requested but Crossref was unreachable; "
                    "no references were verified (all marked unchecked)."
                )
            )
        )
    return report, warnings


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


# --------------------------------------------------------------------------- #
# Supplementary material (plan item 21)
# --------------------------------------------------------------------------- #


def _plain_article_metadata(meta: Meta) -> str:
    """Article-class title block for the one-column supplement.

    REVTeX/IEEE metadata macros (``\\affiliation``, ``\\email``,
    ``\\IEEEauthorblockN``) are undefined in ``article``, so the one-column SI
    needs a plain ``\\title``/``\\author``/``\\maketitle`` block instead. Author
    names and affiliations are flattened into the single ``\\author`` field
    (article has no structured affiliation model); every field is LaTeX-escaped
    at this boundary, exactly like :meth:`Journal.render_metadata`.
    """
    title = escape_latex(meta.title)
    names = ", ".join(escape_latex(a.name) for a in meta.authors)
    affils = " \\\\ ".join(escape_latex(a.name) for a in meta.affiliations)
    # Wrap in a centered \parbox: article's \author centers but does not wrap, so
    # a long author/affiliation list would otherwise overrun the page margins.
    inner = names + (" \\\\[4pt]\\footnotesize " + affils if affils else "")
    author_field = "\\parbox{0.92\\linewidth}{\\centering " + inner + "}"
    return (
        "% Plain-article supplement title block (--supplement-onecolumn).\n"
        f"\\title{{{title}}}\n"
        f"\\author{{{author_field}}}\n"
        "\\date{}\n"
        "\\maketitle\n"
    )


def _emit_supplement(
    supplement_docx_path: Path,
    *,
    output_dir: Path,
    generated_dir: Path,
    figures_dir: Path,
    journal: Journal,
    main_meta: Meta,
    citation_style: str | None,
    crossref_mailto: str | None,
    main_entries: list[RefEntry],
    bib_entries: list[RefEntry] | None = None,
    onecolumn: bool = False,
    exclude_figures: bool = False,
    layout: DocumentLayout | None = None,
    figures_at_end: bool = False,
) -> tuple[SupplementResult, list[RefEntry]]:
    """Emit the supplementary-material project (plan item 21).

    Runs ``supplement_docx_path`` through the same preflight/pandoc/figures/
    citations pipeline the main document just went through, into the SAME
    output tree as a second write-once document: ``supplement.tex`` +
    regenerated ``generated/supplement_*.tex``. Figures land in the shared
    ``figures/`` directory as ``figS<N>.<ext>`` (S-numbered, never colliding
    with the main document's ``fig<N>.<ext>``); citations are extracted the
    same way and merged into ``main_entries`` by
    :func:`latextify.citations.merge.merge_ref_entries` (DOI/raw_id/
    fingerprint identity -- the exact rule used to dedupe within one
    document).

    No metadata guessing runs on the SI docx (plan item 21's explicit
    contract) -- the title block is derived from ``main_meta`` alone
    (``"Supplementary Material: <main title>"``, same authors/affiliations,
    no abstract/keywords).

    Returns the :class:`SupplementResult` plus the merged entries list
    (``main_entries`` untouched at the front, any genuinely-new SI
    references appended) so the caller can rewrite the shared
    ``references.bib``.
    """
    warnings: list[EmitWarning] = []

    # Preflight runs too ("same pipeline" contract) -- findings fold into
    # this function's own warnings (surfaced via the report's Supplement
    # section) rather than the main document's Preflight Findings section.
    si_preflight = run_preflight(supplement_docx_path)
    warnings.extend(
        EmitWarning(
            message=(
                f"supplement preflight [{finding.severity.value}] "
                f"({finding.detector}): {finding.message}"
            )
        )
        for finding in si_preflight.findings
    )

    with tempfile.TemporaryDirectory(prefix="latextify-si-media-") as tmp:
        si_media_dir = Path(tmp)
        si_body_result = convert_docx_to_body(supplement_docx_path, si_media_dir)
        if exclude_figures:
            # Text-only emit: keep the SI consistent with the main document.
            si_figures: tuple[Figure, ...] = ()
            si_figure_files: dict[int, str] = {}
            si_conversion_warnings: tuple[EmitWarning, ...] = ()
            # Clear any S-prefixed images a prior (figure-including) run left.
            _prune_stale_figures(figures_dir, "S", set())
        else:
            si_figures = resolve_overrides(
                extract_figures(supplement_docx_path, si_media_dir),
                supplement_docx_path,
                prefix="S",
            )
            si_figure_files, si_figures, si_conversion_warnings = _copy_figures(
                si_figures, figures_dir, prefix="S"
            )

    si_raw_tex = si_body_result.tex.replace("\r\n", "\n").replace("\r", "\n")
    warnings.extend(
        EmitWarning(message=f"supplement: {finding.message}")
        for finding in si_body_result.findings
    )
    warnings.extend(
        EmitWarning(message=f"supplement: {w.message}") for w in si_conversion_warnings
    )

    si_citation_result = extract_field_citations(supplement_docx_path)
    if si_citation_result.citations:
        si_entries: list[RefEntry] = si_citation_result.entries
        si_citations: tuple[Citation, ...] = tuple(si_citation_result.citations)
    else:
        # No field codes in the SI -> the same plain-text reconstruction
        # safety net the main document uses (item 14). link_body_markers
        # already bakes \cite{<key>} literally into the text, so any
        # cross-document key remap below is applied to the text itself via
        # `remap_cite_keys_in_text` rather than through a Citation list.
        si_entries, si_raw_tex, plaintext_warnings, _plaintext_records = _link_plaintext_citations(
            supplement_docx_path, si_raw_tex, crossref_mailto, bib_entries
        )
        warnings.extend(
            EmitWarning(message=f"supplement: {w.message}") for w in plaintext_warnings
        )
        si_citations = ()

    merged_entries, key_remap = merge_ref_entries(main_entries, si_entries)
    new_reference_count = len(merged_entries) - len(main_entries)

    si_citations = tuple(
        replace(citation, keys=tuple(key_remap.get(k, k) for k in citation.keys))
        for citation in si_citations
    )
    si_raw_tex = remap_cite_keys_in_text(si_raw_tex, key_remap)

    # A one-column plain-article SI has no page-width float, so wide figures
    # resolve to the ordinary single-column environment.
    si_figure_env = _ONECOLUMN_FIGURE_ENV if onecolumn else journal.figure_env
    si_resolved_tex, si_anchor_warnings = resolve_anchors(
        si_raw_tex,
        si_figures,
        si_figure_files,
        si_citations,
        si_figure_env,
        exclude_figures=exclude_figures,
    )
    warnings.extend(
        EmitWarning(message=f"supplement: {w.message}") for w in si_anchor_warnings
    )

    if si_citation_result.citations:
        si_citation_count = len(si_citation_result.citations)
    else:
        si_citation_count = si_resolved_tex.count("\\cite{")

    # -- generated/supplement_preamble.tex: (journal | plain article) + S-numbering --
    si_preamble_text = build_supplement_preamble(
        journal, citation_style, onecolumn=onecolumn, layout=layout, figures_at_end=figures_at_end
    )
    si_preamble_text = si_preamble_text.rstrip("\n") + "\n" + _SUPPLEMENT_NUMBERING
    supplement_preamble_path = generated_dir / "supplement_preamble.tex"
    supplement_preamble_path.write_text(si_preamble_text, encoding="utf-8")

    # -- generated/supplement_metadata.tex: title block only, from main_meta --
    si_meta = replace(
        main_meta,
        title=f"Supplementary Material: {main_meta.title}",
        abstract="",
        keywords=(),
    )
    supplement_metadata_path = generated_dir / "supplement_metadata.tex"
    si_metadata_text = (
        _plain_article_metadata(si_meta) if onecolumn else journal.render_metadata(si_meta)
    )
    supplement_metadata_path.write_text(si_metadata_text, encoding="utf-8")

    # -- generated/supplement_body.tex --
    supplement_body_path = generated_dir / "supplement_body.tex"
    supplement_body_path.write_text(si_resolved_tex, encoding="utf-8")

    # -- generated/supplement_bibliography.tex: reuses the same mechanism as
    # the main document's generated/bibliography.tex (item 26) -- \bibliography
    # only when the SI itself carries a \cite{}, so a citation-free SI still
    # compiles under IEEEtran. BibTeX only pulls entries actually \cite'd in
    # THIS document, so \bibliography{references} here correctly reprints
    # just the SI's own (shared + new) reference list, not the full merged set.
    supplement_bibliography_text = (
        _BIBLIOGRAPHY_LINE if "\\cite{" in si_resolved_tex else _BIBLIOGRAPHY_EMPTY
    )
    supplement_bibliography_path = generated_dir / "supplement_bibliography.tex"
    supplement_bibliography_path.write_text(supplement_bibliography_text, encoding="utf-8")

    # -- supplement.tex: user-owned, write-once, exactly like main.tex --
    supplement_tex_path = output_dir / "supplement.tex"
    supplement_tex_written = not supplement_tex_path.exists()
    if supplement_tex_written:
        supplement_tex_path.write_text(_SUPPLEMENT_TEX_TEMPLATE, encoding="utf-8")

    result = SupplementResult(
        supplement_tex_path=supplement_tex_path,
        supplement_tex_written=supplement_tex_written,
        supplement_preamble_tex_path=supplement_preamble_path,
        supplement_metadata_tex_path=supplement_metadata_path,
        supplement_body_tex_path=supplement_body_path,
        figure_count=len(si_figures),
        citation_count=si_citation_count,
        new_reference_count=new_reference_count,
        warnings=tuple(warnings),
    )
    return result, merged_entries
