"""Anchor resolution: turn the body pipeline's ``%%FIGURE%%`` / ``%%CITE%%`` /
``ZZLTXCITE`` markers into real LaTeX (extracted from :mod:`latextify.emit.project`).

The body LaTeX ``latextify.ingest.pandoc`` produces still carries unresolved
placeholders; this module rewrites them:

    ``%%FIGURE:<n>%%`` -> a ``\\begin{figure}...\\end{figure}`` float (or a bare
        ``\\includegraphics`` for an in-table figure), built from the resolved
        :class:`~latextify.model.figure.Figure`. Handles the two shapes pandoc
        actually emits -- an anchor already wrapped in a (caption-duplicating)
        figure environment, and a bare anchor followed by a leftover
        "Figure N: ..." caption paragraph -- replacing each wholesale so no
        duplicate caption survives. See :func:`_resolve_figure_anchors`.
    ``%%CITE:<idx>%%`` / ``ZZLTXCITE<i>ZZ`` -> ``\\cite{...}`` from the matching
        :class:`~latextify.model.refs.Citation` (the legacy anchor path and the
        Zotero/Mendeley sentinel path, respectively).

:func:`resolve_anchors` is the single entry point the emitter calls;
:func:`citation_linkage_warning` flags citations that were extracted but never
linked, and :func:`remap_cite_keys_in_text` rewrites already-baked ``\\cite{}``
keys (the supplement's cross-document key remap).
"""

from __future__ import annotations

import re

from latextify.citations.bib import escape_latex
from latextify.ingest.citation_sentinels import SENTINEL_RE
from latextify.model.emit import EmitWarning
from latextify.model.figure import Figure
from latextify.model.refs import Citation
from latextify.templates.loader import FigureEnv

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

# Matches an already-resolved \cite{key1,key2} command, used only to remap
# keys baked directly into a supplement's plain-text-reconstructed body
# (plan item 21) -- see `remap_cite_keys_in_text`.
_CITE_KEYS_RE = re.compile(r"\\cite\{([^}]*)\}")


def _figure_block(path: str, caption: str, env: str) -> str:
    caption_line = f"\\caption{{{escape_latex(caption)}}}\n" if caption else ""
    return (
        f"\\begin{{{env}}}\n"
        f"\\centering\n"
        f"\\includegraphics[width=\\linewidth]{{{path}}}\n"
        f"{caption_line}"
        f"\\end{{{env}}}"
    )


#: Width cap for an image resolved inside a table cell. A percentage of
#: ``\linewidth`` is not used here because a plain (non ``p{}``) tabular
#: column has no line-width context of its own -- ``\linewidth`` inside it
#: resolves to the *surrounding text's* width, not the cell's, and would
#: render far larger than the cell can hold. An absolute measurement is
#: deterministic regardless of column type/count, at the cost of not
#: adapting to the actual cell width.
_IN_TABLE_IMAGE_WIDTH = "3cm"


def _in_table_figure(path: str) -> str:
    """Bare, width-limited ``\\includegraphics`` for a figure anchor that
    sits inside a table cell.

    No ``\\begin{figure}...\\end{figure}`` float wrapper and no
    ``\\caption`` -- a float environment is not legal LaTeX inside a
    ``tabular``/``longtable`` cell (``! LaTeX Error: \\begin{figure} on
    input line ... ended by \\end{tabular}.``), and a cell has no caption
    association to begin with (``latextify.figures.extract``'s module
    docstring). Single line, so it is always safe to splice into a table
    row's ``&``-separated cell text.
    """
    return f"\\includegraphics[width={_IN_TABLE_IMAGE_WIDTH}]{{{path}}}"


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
    if figure.in_table:
        return _in_table_figure(path)
    env = figure_env.wide if figure.wide else figure_env.single
    return _figure_block(path, figure.caption, env)


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


def _strip_figure_anchors(tex: str) -> str:
    """Delete every figure float/anchor for a text-only (``exclude_figures``) emit.

    Both anchor shapes the emitter otherwise resolves -- case 1 (pandoc-wrapped
    ``\\begin{figure}...\\end{figure}``) and case 2 (a bare anchor plus any
    leftover "Figure N:" caption paragraph) -- are removed wholesale, so neither
    an ``\\includegraphics`` nor an orphan caption survives. Citation anchors are
    left untouched: ``--exclude-figures`` drops figures only.
    """
    tex = _WRAPPED_FIGURE_RE.sub("", tex)
    tex = _BARE_FIGURE_RE.sub("", tex)
    return tex


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


def resolve_anchors(
    tex: str,
    figures: tuple[Figure, ...],
    figure_files: dict[int, str],
    citations: tuple[Citation, ...],
    figure_env: FigureEnv,
    *,
    exclude_figures: bool = False,
) -> tuple[str, tuple[EmitWarning, ...]]:
    if exclude_figures:
        # Text-only emit: figures were never extracted/copied, so there is
        # nothing to resolve -- just remove their anchors. No figure warnings
        # can arise (an unresolved-anchor warning only makes sense when we were
        # trying to place a figure).
        tex = _strip_figure_anchors(tex)
        figure_warnings: list[EmitWarning] = []
    else:
        figures_by_number = {figure.number: figure for figure in figures}
        tex, figure_warnings = _resolve_figure_anchors(
            tex, figures_by_number, figure_files, figure_env
        )
    tex, citation_warnings = _resolve_citation_anchors(tex, citations)
    tex, sentinel_warnings = _resolve_citation_sentinels(tex, citations)
    return tex, tuple(figure_warnings + citation_warnings + sentinel_warnings)


def citation_linkage_warning(
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


def remap_cite_keys_in_text(tex: str, key_remap: dict[str, str]) -> str:
    """Rewrite already-baked ``\\cite{key1,key2}`` commands through ``key_remap``.

    Only the plain-text citation reconstruction fallback (item 14) bakes
    ``\\cite{...}`` directly into body text before the emitter gets a chance
    to remap keys -- the field-coded/sentinel path instead remaps
    ``Citation.keys`` *before* anchor resolution (see ``_emit_supplement``),
    so it never needs this. A no-op when ``key_remap`` is empty; leaves any
    key not present in ``key_remap`` untouched.
    """
    if not key_remap:
        return tex

    def replace_keys(match: re.Match[str]) -> str:
        keys = [key.strip() for key in match.group(1).split(",")]
        remapped = [key_remap.get(key, key) for key in keys]
        return "\\cite{" + ",".join(remapped) + "}"

    return _CITE_KEYS_RE.sub(replace_keys, tex)
