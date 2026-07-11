"""Project emitter: write the output LaTeX project tree (plan item 5).

Ties every already-merged stage into one pipeline -- see the module
docstring of :mod:`latextify.emit` for the output tree contract:

    ingest.metadata_guess     -- Meta (title/authors/abstract/keywords)
    ingest.pandoc             -- body LaTeX with ``%%FIGURE:<n>%%`` /
                                  ``%%CITE:<idx>%%`` anchors unresolved
    figures.extract/override  -- resolved Figure IR (embedded vs override)
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

``%%CITE:<idx>%%`` anchors are 1-based (planted by
``latextify.ingest.filters.plant_anchors``); ``Citation.index`` values from
``latextify.citations.fields.extract_field_citations`` are 0-based
(document-order ``enumerate``). Anchor ``idx`` pairs with
``citations[idx - 1]``.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from latextify.citations.bib import entries_to_bib, escape_latex
from latextify.citations.fields import extract_field_citations
from latextify.emit.metadata import load_meta, write_metadata_tex
from latextify.figures.extract import extract_figures
from latextify.figures.override import resolve_overrides
from latextify.ingest.pandoc import convert_docx_to_body
from latextify.model.emit import EmitResult, EmitWarning
from latextify.model.figure import Figure
from latextify.model.refs import Citation
from latextify.templates import loader as templates_loader
from latextify.templates.loader import FigureEnv

_MAIN_TEX_TEMPLATE = (
    "\\input{generated/preamble}\n"
    "\\begin{document}\n"
    "\\input{generated/metadata}\n"
    "\\input{generated/body}\n"
    "\\bibliography{references}\n"
    "\\end{document}\n"
)

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

    journal = templates_loader.load(journal_name, journals_dir=journals_dir)
    meta = load_meta(docx_path)

    with tempfile.TemporaryDirectory(prefix="latextify-media-") as tmp:
        media_dir = Path(tmp)
        body_result = convert_docx_to_body(docx_path, media_dir)
        figures = resolve_overrides(extract_figures(docx_path, media_dir), docx_path)
        figure_files = _copy_figures(figures, figures_dir)

    citation_result = extract_field_citations(docx_path)
    bib_text = entries_to_bib(citation_result.entries)

    # pandoc's LaTeX writer emits CRLF on Windows; the bare-anchor regex below
    # matches literal "\n" boundaries, so normalize before resolving anchors.
    raw_tex = body_result.tex.replace("\r\n", "\n").replace("\r", "\n")
    resolved_tex, warnings = _resolve_anchors(
        raw_tex, figures, figure_files, citation_result.citations, journal.figure_env
    )
    warnings += _citation_linkage_warning(citation_result.citations, resolved_tex)

    preamble_text = _ensure_hyperref(journal.render_preamble(mode=citation_style))
    (generated_dir / "preamble.tex").write_text(preamble_text, encoding="utf-8")

    metadata_tex_path = write_metadata_tex(generated_dir, meta, journal)

    body_tex_path = generated_dir / "body.tex"
    body_tex_path.write_text(resolved_tex, encoding="utf-8")

    bib_path = output_dir / "references.bib"
    bib_path.write_text(bib_text, encoding="utf-8")

    main_tex_path = output_dir / "main.tex"
    main_tex_written = not main_tex_path.exists()
    if main_tex_written:
        main_tex_path.write_text(_MAIN_TEX_TEMPLATE, encoding="utf-8")

    return EmitResult(
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
        citation_count=len(citation_result.citations),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Figure file copying
# --------------------------------------------------------------------------- #


def _copy_figures(figures: tuple[Figure, ...], figures_dir: Path) -> dict[int, str]:
    """Copy each figure's resolved file into ``figures_dir`` as ``fig<N><ext>``.

    Returns a map of figure number -> the forward-slashed, LaTeX-relative
    path (``figures/fig<N><ext>``) to embed in the body.
    """
    files: dict[int, str] = {}
    for figure in figures:
        src = figure.resolved_path
        dest_name = f"fig{figure.number}{src.suffix.lower()}"
        shutil.copy2(src, figures_dir / dest_name)
        files[figure.number] = f"figures/{dest_name}"
    return files


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
        citation = by_position.get(idx)
        if citation is None or not citation.keys:
            warnings.append(
                EmitWarning(
                    message=f"unresolved citation anchor {idx}: no matching citation record"
                )
            )
            return (
                f"% LATEXTIFY WARNING: unresolved citation anchor {idx}\n"
                f"\\textbf{{[UNRESOLVED CITATION]}}"
            )
        return f"\\cite{{{','.join(citation.keys)}}}"

    tex = _CITE_RE.sub(replace, tex)
    return tex, warnings


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
    return tex, tuple(figure_warnings + citation_warnings)


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
