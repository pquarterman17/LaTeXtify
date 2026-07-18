"""Portable (non-LaTeX) figure/citation anchor markers (FORMATS_AND_PRIVACY
items 4-5).

Kept out of :mod:`latextify.ingest.filters` (already large and
size-ratchet-pinned at its ceiling) even though it is conceptually this
module's sibling: :func:`~latextify.ingest.filters.plant_anchors` plants
the LaTeX-target anchors :func:`~latextify.ingest.pandoc.convert_docx_to_body`
uses; :func:`plant_portable_anchors` plants the equivalent markers for any
OTHER pandoc writer target, used by the HTML/Markdown export pipeline
(:mod:`latextify.emit.alt_formats`) via
:func:`~latextify.ingest.pandoc.convert_docx_to_ast`.
"""

from __future__ import annotations

import panflute as pf

from latextify.ingest.filters import AnchorCounts

#: Portable marker prefixes, distinct from ``latextify.ingest.filters.
#: plant_anchors``'s LaTeX-raw ``%%FIGURE:``/``%%CITE:`` anchors so a doc run
#: through BOTH (never happens in practice) can't collide.
_PORTABLE_FIGURE_PREFIX = "%%FIGURE:"
_PORTABLE_CITE_PREFIX = "%%CITE:"


def plant_portable_anchors(doc: pf.Doc) -> tuple[pf.Doc, AnchorCounts]:
    """Replace Image/Cite nodes with plain-text anchor markers (non-LaTeX writers).

    ``plant_anchors`` emits ``panflute.RawInline(format="latex")`` markers --
    correct for the LaTeX writer, but silently DROPPED by pandoc's HTML writer
    and reduced to a `` `%%FIGURE:1%%`{=latex} `` raw code span by its Markdown
    writer (verified empirically; see ``latextify.emit.alt_formats``'s module
    docstring). This is the same idea targeted at any OTHER pandoc writer: a
    plain ``panflute.Str`` survives every writer's text escaping unchanged
    (the marker text uses no character any writer treats specially).

    A native ``Figure`` block -- pandoc's docx reader promotes a Word
    "Caption"-styled solo image into one, with its OWN (uncleaned, possibly
    empty) caption -- is unwrapped to its bare content instead of just having
    its inner Image replaced, so the block's duplicate caption never reaches
    the rendered output; ``latextify.emit.alt_formats`` rebuilds the caption
    from the SAME reconciled ``Figure`` records the LaTeX emitter uses.
    Because ``Doc.walk`` is post-order, an Image's action always fires before
    its enclosing Figure block's, so the unwrap sees (and discards) the
    marker that already replaced it -- the figure is still counted exactly
    once, in the same document-order sequence
    ``latextify.figures.extract.extract_figures`` uses (every ``Image``
    node, regardless of Figure-block wrapping).

    Numbered 1-based in document order, same scheme as ``plant_anchors``.
    Mutates ``doc`` in place; also returns it (with the counts) for chaining.
    """
    counts = AnchorCounts()

    def action(elem: pf.Element, doc: pf.Doc) -> pf.Element | list | None:
        if isinstance(elem, pf.Figure):
            return list(elem.content)
        if isinstance(elem, pf.Image):
            counts.figures += 1
            return pf.Str(f"{_PORTABLE_FIGURE_PREFIX}{counts.figures}%%")
        if isinstance(elem, pf.Cite):
            counts.citations += 1
            return pf.Str(f"{_PORTABLE_CITE_PREFIX}{counts.citations}%%")
        return None

    doc = doc.walk(action)
    return doc, counts
