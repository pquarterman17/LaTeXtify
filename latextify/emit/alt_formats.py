"""Self-contained HTML and plain Markdown export (FORMATS_AND_PRIVACY items 4-5).

:func:`export_html` and :func:`export_markdown` are the two public entry
points. Both reuse the SAME AST-reading half of the body pipeline the LaTeX
emitter uses (:func:`latextify.ingest.pandoc.convert_docx_to_ast`) and the
SAME reconciled figures/citations (:mod:`latextify.figures.extract` +
:mod:`latextify.figures.override`, :mod:`latextify.citations.fields` +
:mod:`latextify.citations.plaintext`) -- so the reference list and figures
in an HTML/Markdown export match what :func:`latextify.emit.project.
emit_project` would produce for the same manuscript, without ever going
through the LaTeX writer. The writer-target-specific leaf functions
(reference-list formatting, marker resolution, page assembly) live in the
sibling module :mod:`latextify.emit.alt_formats_render`, split out purely to
keep both files under the repo's size ceiling.

Why not just reuse the LaTeX body pipeline's anchors: ``plant_anchors``
(:mod:`latextify.ingest.filters`) marks each Image/Cite with a
``panflute.RawInline(format="latex")`` -- correct for the LaTeX writer, but
verified empirically to be silently DROPPED by pandoc's HTML writer and
reduced to a `` `%%FIGURE:1%%`{=latex} `` raw code span by its Markdown
writer. This module instead calls :func:`~latextify.ingest.portable_anchors.
plant_portable_anchors`, whose plain ``panflute.Str`` markers survive any
writer's text escaping unchanged, then resolves them itself with
writer-appropriate regexes (mirroring :mod:`latextify.emit.anchors`, just
targeting HTML/Markdown syntax instead of LaTeX macros). Tables are NOT
run through :func:`~latextify.ingest.filters.normalize_tables` (which
hard-codes a LaTeX booktabs reconstruction) -- pandoc's own native
HTML/Markdown table writer handles a ``Table`` AST node directly, including
pathological (merged-cell) tables, which it degrades to a raw HTML
``<table>`` block automatically even inside the Markdown writer. This is a
deliberate simplification: the LaTeX path's table reconstruction exists
solely to satisfy Tectonic's fragment-mode compile constraints, which have
no equivalent for a browser or a Markdown viewer.

Simplifications this round (reported, not hidden):

    * In-text citation LINKS are wired for the field-coded citation path
      (Zotero/Mendeley/EndNote/Word-native -- the common case) only. A
      manuscript with no citation field codes still gets a numbered
      reference list (built the same way the LaTeX emitter's fallback
      does, via :func:`latextify.citations.plaintext.reconstruct_citations`).
      Its own typed reference list IS stripped from the body -- at the AST
      level, before the writer runs, using the SAME heading-keyword
      classification :func:`~latextify.citations.plaintext.
      reconstruct_citations` used to find that list in the first place (see
      ``_strip_reconstructed_reference_section`` below) -- so the output
      never carries two reference lists. In-text markers, however, are left
      exactly as typed and NOT linked to the reconstructed list:
      :mod:`latextify.citations.plaintext`'s marker-linking regexes match
      pandoc's ESCAPED LATEX text specifically (``{[}12{]}``,
      ``\\textsuperscript{...}``) and have no HTML/Markdown equivalent;
      porting them is out of scope this round. A warning noting the
      unlinked markers is emitted when this path is taken.
    * Figure embedding (HTML): PNG/JPG/JPEG/GIF/WEBP/BMP and SVG figures are
      embedded as base64 ``data:`` URIs; PDF/EPS/TIFF figures (routine in a
      LaTeX-oriented workflow, meaningless to a browser ``<img>``) are NOT
      embedded -- the figure's caption still appears, with a warning that
      the image could not be embedded. Word's ``a:srcRect`` display crop
      (applied on the LaTeX path by ``latextify.figures.convert``) is not
      applied here.
    * Figure files (Markdown): copied as-is next to the output file (in a
      ``<stem>_files/`` folder) and referenced with a relative path; no
      format conversion or cropping.
    * No journal/layout options: HTML/Markdown have no equivalent of a
      LaTeX document class or column layout, so none of ``emit_project``'s
      journal/anonymize/columns/figures-at-end/supplement options apply
      here. A minimal title/author/abstract block (from the same
      ``paper.yaml`` sidecar the LaTeX path guesses/loads) is prepended
      instead.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

import panflute as pf
import pypandoc

from latextify.citations.fields import extract_field_citations
from latextify.citations.plaintext import is_reference_heading_text, reconstruct_citations
from latextify.citations.refs_import import parse_references_file
from latextify.emit.alt_formats_render import (
    PORTABLE_FIGURE_HTML_RE,
    PORTABLE_FIGURE_MD_RE,
    assemble_html,
    assemble_markdown,
    embed_data_uri,
    render_html_citation,
    render_html_figure,
    render_markdown_citation,
    render_markdown_figure,
    render_reference_list_html,
    render_reference_list_markdown,
    resolve_citation_markers,
    resolve_figure_markers,
)
from latextify.emit.metadata import load_meta
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.citation_sentinels import SENTINEL_RE
from latextify.ingest.pandoc import convert_docx_to_ast
from latextify.ingest.portable_anchors import plant_portable_anchors
from latextify.model import ExportResult, FilterFinding, Meta
from latextify.model.emit import EmitWarning
from latextify.model.figure import Figure
from latextify.model.refs import Citation, RefEntry

# --------------------------------------------------------------------------- #
# Title/author/abstract block
# --------------------------------------------------------------------------- #


def _text_inlines(text: str) -> list[pf.Element]:
    """Plain Str/Space inline run from a text string (no markup interpretation)."""
    inlines: list[pf.Element] = []
    for i, word in enumerate(text.split()):
        if i:
            inlines.append(pf.Space())
        inlines.append(pf.Str(word))
    return inlines


def _insert_title_block(doc: pf.Doc, meta: Meta, *, visible_header: bool) -> None:
    """Set the title/author metadata and (for Markdown) insert a visible header.

    pandoc's docx reader auto-populates ``doc.metadata['title']`` from the
    source file's own document properties -- always overwritten here so both
    export targets show the SAME title ``emit_project``'s LaTeX path uses
    (the guessed/loaded ``paper.yaml`` title), not whatever raw property the
    docx happened to carry.

    HTML is converted with ``--standalone``, which renders title/author from
    ``doc.metadata`` itself in pandoc's own
    ``<header id="title-block-header">`` block -- inserting a second, manual
    ``Header``/``Para`` for it would duplicate that block (verified: pandoc's
    docx reader sets ``title`` metadata even when this function had not set
    it), so ``visible_header=False`` skips it for that target. Markdown is
    never converted with ``--standalone`` (see ``_export``), so it never
    reads ``doc.metadata`` at all -- ``visible_header=True`` builds the same
    block by hand instead. Either way, an abstract (no pandoc metadata
    equivalent for HTML) is always inserted as a visible ``BlockQuote`` when
    present.
    """
    doc.metadata["title"] = pf.MetaString(meta.title)
    if meta.authors:
        doc.metadata["author"] = pf.MetaList(*(pf.MetaString(a.name) for a in meta.authors))
    blocks: list[pf.Element] = []
    if visible_header:
        blocks.append(pf.Header(*_text_inlines(meta.title), level=1))
        if meta.authors:
            blocks.append(pf.Para(*_text_inlines(", ".join(a.name for a in meta.authors))))
    if meta.abstract:
        blocks.append(pf.BlockQuote(pf.Para(*_text_inlines(meta.abstract))))
    if blocks:
        doc.content = blocks + list(doc.content)


# --------------------------------------------------------------------------- #
# Reconciled citations (shared selection logic; formatting lives in
# alt_formats_render)
# --------------------------------------------------------------------------- #


def _prepare_reference_data(
    docx_path: Path,
    *,
    crossref_mailto: str | None,
    bib_entries: list[RefEntry] | None,
) -> tuple[list[RefEntry], tuple[Citation, ...], bool, bool, list[EmitWarning]]:
    """Mirror ``emit_project``'s citation-path selection: field-coded citations
    win when present, else plain-text reconstruction (item 14's fallback) --
    reusing the exact same extraction functions the LaTeX emitter calls.

    Returns ``(entries, citations, field_coded, strip_typed_list, warnings)``.
    ``citations`` is only non-empty on the field-coded path. ``strip_typed_list``
    is ``True`` only on the plain-text path when a reference list was actually
    reconstructed (``PlaintextResult.has_reference_list``) -- the caller uses it
    to gate ``_strip_reconstructed_reference_section``, which removes the SAME
    typed reference list from the AST so the output does not carry it twice; see
    the module docstring's "in-text citation LINKS" simplification for why the
    plaintext path's markers are still left unlinked.
    """
    citation_result = extract_field_citations(docx_path)
    if citation_result.citations:
        return citation_result.entries, tuple(citation_result.citations), True, False, []

    plaintext_result = reconstruct_citations(
        docx_path, mailto=crossref_mailto, bib_entries=bib_entries
    )
    warnings: list[EmitWarning] = []
    if plaintext_result.has_reference_list:
        warnings.append(
            EmitWarning(
                message=(
                    "no citation field codes found; a numbered reference list was "
                    "reconstructed from the manuscript's typed bibliography (the "
                    "typed list itself was removed from the body to avoid a "
                    "duplicate), but in-text citation markers were left exactly "
                    "as typed -- not linked to the reconstructed list; only the "
                    "LaTeX export (latextify.emit.project) rewrites plain-text "
                    "citation markers into linked references."
                )
            )
        )
    return (
        plaintext_result.entries,
        (),
        False,
        plaintext_result.has_reference_list,
        warnings,
    )


def _strip_reconstructed_reference_section(doc: pf.Doc) -> None:
    """Remove the typed reference-list ``Header`` (and everything after it).

    Called ONLY when ``_prepare_reference_data``'s ``strip_typed_list`` is
    ``True`` -- i.e. the plain-text citation path found and reconstructed a
    typed reference list (see :func:`~latextify.citations.plaintext.
    reconstruct_citations`). That reconstructed list is appended separately
    (:func:`~latextify.emit.alt_formats_render.render_reference_list_html` /
    ``..._markdown``), so leaving the manuscript's own typed list in the body
    would duplicate it. This is the AST-level counterpart of
    :func:`~latextify.citations.plaintext.strip_reference_section_to_eof`
    (which cuts already-rendered LaTeX text instead): it scans the top-level
    blocks of ``doc.content`` for the first ``Header`` whose stringified text
    reads as a reference-list heading -- using
    :func:`~latextify.citations.plaintext.is_reference_heading_text`, the SAME
    keyword/length classification :func:`~latextify.citations.plaintext.
    segment_reference_list` used to find that heading in the raw manuscript in
    the first place -- and drops it plus every block after it, to EOF.
    Writer-agnostic (mutates the AST before either the HTML or the Markdown
    writer ever sees it), unlike porting ``plaintext.py``'s LaTeX-text regexes
    would have been. Mutates ``doc.content`` in place; a no-op if no matching
    ``Header`` is found (the AST's own heading detection did not line up with
    what ``segment_reference_list`` found in the raw OOXML -- not expected in
    practice, but never a crash either way).
    """
    blocks = list(doc.content)
    for index, block in enumerate(blocks):
        if isinstance(block, pf.Header) and is_reference_heading_text(pf.stringify(block).strip()):
            doc.content = blocks[:index]
            return


# --------------------------------------------------------------------------- #
# Shared export core
# --------------------------------------------------------------------------- #


def _export(
    docx_path: Path | str,
    output_path: Path | str,
    *,
    target: str,
    crossref_mailto: str | None,
    references_bib_path: Path | str | None,
) -> ExportResult:
    docx_path = Path(docx_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bib_entries = (
        parse_references_file(references_bib_path) if references_bib_path is not None else None
    )
    meta = load_meta(docx_path)
    entries, citations, _field_coded, strip_typed_list, warnings = _prepare_reference_data(
        docx_path, crossref_mailto=crossref_mailto, bib_entries=bib_entries
    )
    number_by_key = {entry.key: i for i, entry in enumerate(entries, start=1)}

    figures: tuple[Figure, ...] = ()
    figure_embeds: dict[int, str | None] = {}
    figure_rel_paths: dict[int, str | None] = {}
    shared_findings: list[FilterFinding] = []

    with tempfile.TemporaryDirectory(prefix="latextify-altfmt-") as tmp:
        media_dir = Path(tmp)
        doc, shared_findings = convert_docx_to_ast(docx_path, media_dir, strip_front_matter=True)
        figures = resolve_overrides(extract_figures(docx_path, media_dir), docx_path)
        if strip_typed_list:
            _strip_reconstructed_reference_section(doc)
        doc, _anchor_counts = plant_portable_anchors(doc)
        _insert_title_block(doc, meta, visible_header=target != "html")

        buf = io.StringIO()
        pf.dump(doc, buf)
        json_str = buf.getvalue()

        if target == "html":
            body_text = pypandoc.convert_text(
                json_str,
                to="html",
                format="json",
                extra_args=["--mathml", "--standalone", "--embed-resources"],
                # "json"/"html" are always-valid pandoc format names here --
                # see latextify.ingest.pandoc for the full reasoning behind
                # skipping pypandoc's own (uncached, 2-subprocess) check.
                verify_format=False,
            )
            for figure in figures:
                figure_embeds[figure.number] = embed_data_uri(figure.resolved_path)
        else:
            body_text = pypandoc.convert_text(
                json_str, to="markdown", format="json", verify_format=False
            )
            media_out_dir = output_path.parent / f"{output_path.stem}_files"
            for figure in figures:
                src = figure.resolved_path
                dest_name = f"fig{figure.number}{src.suffix.lower()}"
                try:
                    media_out_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, media_out_dir / dest_name)
                    figure_rel_paths[figure.number] = f"{media_out_dir.name}/{dest_name}"
                except OSError:
                    figure_rel_paths[figure.number] = None

    # pandoc's writer emits CRLF on Windows; the marker-resolution regexes
    # (alt_formats_render) match literal "\n" paragraph boundaries -- same
    # normalization latextify.emit.project applies to the LaTeX body before
    # its own anchor resolution.
    body_text = body_text.replace("\r\n", "\n").replace("\r", "\n")

    warnings.extend(EmitWarning(message=f.message) for f in shared_findings)
    for figure in figures:
        if target == "html" and figure_embeds.get(figure.number) is None:
            warnings.append(
                EmitWarning(
                    message=(
                        f"figure {figure.number}: {figure.resolved_path.name} could not be "
                        "embedded in the self-contained HTML export (only "
                        "PNG/JPEG/GIF/WEBP/BMP/SVG are supported this round); the caption "
                        "is shown without an image."
                    )
                )
            )
        if target == "markdown" and figure_rel_paths.get(figure.number) is None:
            warnings.append(
                EmitWarning(
                    message=(
                        f"figure {figure.number}: {figure.resolved_path.name} could not be "
                        "copied alongside the Markdown export; the caption is shown "
                        "without an image."
                    )
                )
            )

    if target == "html":
        body_text, fig_warnings = resolve_figure_markers(
            body_text,
            PORTABLE_FIGURE_HTML_RE,
            figures,
            lambda figure: render_html_figure(figure, figure_embeds.get(figure.number)),
        )
        body_text, cite_warnings = resolve_citation_markers(
            body_text, citations, number_by_key, SENTINEL_RE, render_html_citation
        )
        full = assemble_html(body_text, render_reference_list_html(entries))
    else:
        body_text, fig_warnings = resolve_figure_markers(
            body_text,
            PORTABLE_FIGURE_MD_RE,
            figures,
            lambda figure: render_markdown_figure(figure, figure_rel_paths.get(figure.number)),
        )
        body_text, cite_warnings = resolve_citation_markers(
            body_text, citations, number_by_key, SENTINEL_RE, render_markdown_citation
        )
        full = assemble_markdown(body_text, render_reference_list_markdown(entries))

    warnings.extend(fig_warnings)
    warnings.extend(cite_warnings)

    output_path.write_text(full, encoding="utf-8")

    return ExportResult(
        output_path=output_path,
        figure_count=len(figures),
        citation_count=len(entries),
        warnings=tuple(warnings),
    )


def export_html(
    docx_path: Path | str,
    output_path: Path | str,
    *,
    crossref_mailto: str | None = None,
    references_bib_path: Path | str | None = None,
) -> ExportResult:
    """Export ``docx_path`` to a single self-contained ``.html`` file.

    Math is rendered as native MathML (``--mathml``); the page is
    ``--standalone`` with ``--embed-resources`` so it opens offline with no
    external ``src=``/``href=`` reference -- figures are embedded as base64
    ``data:`` URIs (see the module docstring for the raster/SVG-only
    limitation). Citations are resolved against the SAME reconciled
    reference list :func:`latextify.emit.project.emit_project` would build
    for this manuscript; see :func:`_prepare_reference_data` for the
    field-coded-vs-plaintext selection.

    Args:
        docx_path: source manuscript (.docx/.odt/.rtf/.md).
        output_path: the ``.html`` file to write (parent directories
            created as needed).
        crossref_mailto: contact address for Crossref reconciliation on the
            plain-text citation fallback (no citation field codes); see
            ``emit_project``'s parameter of the same name.
        references_bib_path: optional ``.bib``/``.ris``/... export of the
            author's reference manager, consulted before Crossref on the
            plain-text fallback; see ``emit_project``'s ``references_bib_path``.

    Returns:
        An :class:`~latextify.model.ExportResult` naming the written file
        plus any warnings (unresolved anchors, un-embeddable figures, the
        plain-text-citation-path caveat).
    """
    return _export(
        docx_path,
        output_path,
        target="html",
        crossref_mailto=crossref_mailto,
        references_bib_path=references_bib_path,
    )


def export_markdown(
    docx_path: Path | str,
    output_path: Path | str,
    *,
    crossref_mailto: str | None = None,
    references_bib_path: Path | str | None = None,
) -> ExportResult:
    """Export ``docx_path`` to a single plain ``.md`` file.

    Math stays literal LaTeX (``$...$``/``$$...$$``, pandoc's own default for
    the Markdown writer -- no MathJax/KaTeX dependency needed to view it).
    Figures are copied next to ``output_path`` (in a ``<stem>_files/``
    folder) and referenced as ``![caption](path)``; a reconciled numbered
    reference list is appended. See :func:`export_html` for the shared
    citation-reconciliation behavior and the module docstring for this
    round's simplifications.

    Args:
        docx_path: source manuscript (.docx/.odt/.rtf/.md).
        output_path: the ``.md`` file to write (parent directories created
            as needed).
        crossref_mailto: see :func:`export_html`.
        references_bib_path: see :func:`export_html`.

    Returns:
        An :class:`~latextify.model.ExportResult` naming the written file
        plus any warnings.
    """
    return _export(
        docx_path,
        output_path,
        target="markdown",
        crossref_mailto=crossref_mailto,
        references_bib_path=references_bib_path,
    )
