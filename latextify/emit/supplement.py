"""Emit the Supplementary Material project (plan item 21).

Split out of :mod:`latextify.emit.project` (2026-08-10): a second manuscript
runs through this exact same pipeline (preflight, pandoc body, figures,
citations) as the main document, into the SAME output tree, as a second
write-once document -- ``supplement.tex`` + ``generated/supplement_*.tex`` --
built by :func:`emit_supplement`. It is not a smaller variant of
``emit_project``; it is a second, independent walk through the same stages
with different endpoints (S-numbered figures, a merged-not-separate
bibliography, no metadata guessing), so pulling it out from beside
``emit_project`` -- rather than folding its steps into a flag-driven branch of
that function -- keeps each function reading as one linear pipeline instead of
two interleaved ones.

Two things make this document not just "the main document again":

    * Figures share ``figures/`` with the main document under an ``S``
      prefix (``figS<N>.<ext>``, via ``prefix="S"`` threaded through
      ``figures.override``/``emit.figures_copy``), so neither document's
      generated files can collide with or overwrite the other's, or with a
      user's own file of the same name.
    * Citations are merged into the shared ``references.bib`` by
      :func:`latextify.citations.merge.merge_ref_entries`, which reuses
      ``citations.fields.dedup_identity`` so a reference cited in both
      documents (matched by DOI, source id, or author/year/title fingerprint)
      collapses to one shared entry rather than appearing twice under two
      keys. When the SI has no field codes of its own, its typed reference
      list goes through the exact same plain-text reconstruction fallback the
      main document uses (:func:`latextify.emit.citation_resolution.link_plaintext_citations`,
      plan item 14) before the merge -- but that path already bakes
      ``\\cite{key}`` literally into the SI body text, so a key that the merge
      renamed is fixed up afterward with :func:`latextify.emit.anchors.remap_cite_keys_in_text`
      rather than through a ``Citation`` list rewrite (the field-coded path's
      ``Citation.keys`` are remapped structurally instead, before anchor
      resolution, since a ``Citation`` object still exists to hold them at
      that point).

No metadata guessing runs on the SI docx (plan item 21's explicit contract):
the title block is derived from the main document's already-loaded ``Meta``
alone (``"Supplementary Material: <main title>"``, same authors/affiliations,
no abstract/keywords) -- see :func:`_plain_article_metadata` for the
plain-``article``-class rendering a one-column SI needs, since REVTeX/IEEE
metadata macros (``\\affiliation``, ``\\IEEEauthorblockN``) are undefined
there.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from latextify.citations.bib import escape_latex
from latextify.citations.fields import extract_field_citations
from latextify.citations.merge import merge_ref_entries
from latextify.emit.anchors import remap_cite_keys_in_text, resolve_anchors
from latextify.emit.bibliography import BIBLIOGRAPHY_EMPTY, BIBLIOGRAPHY_LINE
from latextify.emit.citation_resolution import link_plaintext_citations
from latextify.emit.figures_copy import _copy_figures, _prune_stale_figures
from latextify.emit.submission import (
    _ONECOLUMN_FIGURE_ENV,
    DocumentLayout,
    build_supplement_preamble,
)
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.pandoc import convert_docx_to_body
from latextify.ingest.preflight import run_preflight
from latextify.model.emit import EmitWarning, SupplementResult
from latextify.model.figure import Figure
from latextify.model.meta import Meta
from latextify.model.refs import Citation, RefEntry
from latextify.templates.loader import Journal

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


def emit_supplement(
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
    strip_figure_metadata: bool = True,
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
                si_figures, figures_dir, prefix="S", strip_metadata=strip_figure_metadata
            )

    si_raw_tex = si_body_result.tex.replace("\r\n", "\n").replace("\r", "\n")
    warnings.extend(
        EmitWarning(message=f"supplement: {finding.message}") for finding in si_body_result.findings
    )
    warnings.extend(EmitWarning(message=f"supplement: {w.message}") for w in si_conversion_warnings)

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
        si_entries, si_raw_tex, plaintext_warnings, _plaintext_records = link_plaintext_citations(
            supplement_docx_path, si_raw_tex, crossref_mailto, bib_entries
        )
        warnings.extend(EmitWarning(message=f"supplement: {w.message}") for w in plaintext_warnings)
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
    warnings.extend(EmitWarning(message=f"supplement: {w.message}") for w in si_anchor_warnings)

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
        BIBLIOGRAPHY_LINE if "\\cite{" in si_resolved_tex else BIBLIOGRAPHY_EMPTY
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
