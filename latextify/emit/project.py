"""Project emitter: write the output LaTeX project tree (plan item 5).

Ties every already-merged stage into one pipeline -- see the module
docstring of :mod:`latextify.emit` for the output tree contract:

    ingest.metadata_guess     -- Meta (title/authors/abstract/keywords)
    ingest.pandoc             -- body LaTeX with ``%%FIGURE:<n>%%`` /
                                  ``%%CITE:<idx>%%`` anchors unresolved
    figures.extract/override  -- resolved Figure IR (manifest/folder/embedded)
    emit.figures_copy         -- what lands in figures/: SVG/EPS -> PDF (item
                                  15), TIFF -> PNG, the figure metadata strip
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
manuscript runs through this same pipeline into the SAME output tree as a
second write-once document -- see :mod:`latextify.emit.supplement` for the
S-prefixed figures, the cross-document reference merge/dedup, and the rest of
that document's own contract. Omitting ``supplement_docx_path`` leaves the
main document's output byte-identical to before item 21.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from latextify.citations.bib import entries_to_bib
from latextify.citations.body_markers import strip_reference_section_to_eof
from latextify.citations.fields import extract_field_citations
from latextify.citations.refs_import import parse_references_file
from latextify.emit.anchors import citation_linkage_warning, resolve_anchors
from latextify.emit.bibliography import (
    BIBLIOGRAPHY_EMPTY,
    BIBLIOGRAPHY_LINE,
    legacy_bibliography_warning,
)
from latextify.emit.citation_resolution import link_plaintext_citations, run_reference_validation
from latextify.emit.figures_copy import _copy_figures, _prune_stale_figures
from latextify.emit.metadata import load_meta, write_metadata_tex
from latextify.emit.submission import (
    DocumentLayout,
    anonymize_meta,
    build_main_preamble,
    strip_acknowledgments,
)
from latextify.emit.supplement import emit_supplement
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.formats import non_docx_warnings
from latextify.ingest.metadata_guess import sidecar_path_for
from latextify.ingest.pandoc import convert_docx_to_body
from latextify.ingest.preflight import run_preflight
from latextify.model.emit import EmitResult, EmitWarning, SupplementResult
from latextify.model.figure import Figure
from latextify.model.reconcile import ReconciliationReport
from latextify.model.refs import RefEntry
from latextify.model.validate import ValidationReport
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader

_MAIN_TEX_TEMPLATE = (
    "\\input{generated/preamble}\n"
    "\\begin{document}\n"
    "\\input{generated/metadata}\n"
    "\\input{generated/body}\n"
    "\\input{generated/bibliography}\n"
    "\\end{document}\n"
)


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
    strip_figure_metadata: bool = True,
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
        strip_figure_metadata: when True (default), every raster figure written
            into ``figures/`` has its embedded metadata removed losslessly --
            EXIF, GPS, camera/lens serial numbers, the capture thumbnail (which
            can still show an *uncropped* original), and PNG text chunks. The
            pixels and the ICC profile are untouched. ``figures/`` ships as
            source to arXiv and journal submission systems, so this is on by
            default; ``latextify convert --keep-figure-metadata`` turns it off
            for an author who wants their figure metadata preserved.
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
            figure_files, figures, conversion_warnings = _copy_figures(
                figures, figures_dir, strip_metadata=strip_figure_metadata
            )

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
        entries, resolved_tex, plaintext_warnings, plaintext_records = link_plaintext_citations(
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

    bibliography_tex = BIBLIOGRAPHY_LINE if bib_text.strip() else BIBLIOGRAPHY_EMPTY
    (generated_dir / "bibliography.tex").write_text(bibliography_tex, encoding="utf-8")

    main_tex_path = output_dir / "main.tex"
    main_tex_written = not main_tex_path.exists()
    if main_tex_written:
        main_tex_path.write_text(_MAIN_TEX_TEMPLATE, encoding="utf-8")
    else:
        warnings.extend(legacy_bibliography_warning(main_tex_path))

    # Supplementary material (plan item 21): a second write-once document
    # sharing this project's figures/ and references.bib. Emitted before the
    # EmitResult/report are built so the (possibly bib-merging) outcome
    # folds into one result object and one final report write.
    supplement_result: SupplementResult | None = None
    if supplement_docx_path is not None:
        supplement_result, entries = emit_supplement(
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
            strip_figure_metadata=strip_figure_metadata,
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
        validation, validation_warnings = run_reference_validation(entries, crossref_mailto)
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
