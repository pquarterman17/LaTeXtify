"""Media <-> figure-number <-> caption association (plan item 9).

:func:`extract_figures` is the entry point. It runs its own docx -> pandoc
JSON AST conversion (independent of ``latextify.ingest.pandoc``, which
already extracts media for the body pipeline but discards the AST after
producing LaTeX text) so it can inspect the pre-anchor-replacement tree for
each ``Image`` node and its surrounding paragraphs. Pointed at the same
``media_dir`` the body pipeline used, pandoc's ``--extract-media`` is
idempotent -- re-running it writes the same files again -- so calling this
after :func:`latextify.ingest.pandoc.convert_docx_to_body` is safe and cheap
for the manuscript sizes this project targets.

Numbering contract: figures are numbered 1-based by encounter order over the
FULL document AST (``doc.walk()``), not just the top-level block list -- this
must match ``latextify.ingest.filters.plant_anchors``, which numbers
``Image`` nodes the same way. Both functions drive the count off
``panflute.Doc.walk()`` itself (rather than two independently hand-rolled
tree walks that could silently drift apart in traversal order) so an image
nested inside a table cell (``panflute.TableCell``) is counted in its true
document position instead of being skipped -- skipping it here while
``plant_anchors`` still counts it in the shared body pipeline was exactly the
bug this module used to have: a table-nested image's ``%%FIGURE:<n>%%``
anchor had no matching ``Figure`` record, and every subsequent top-level
figure's number was off by one. The emitter (item 5) keys ``%%FIGURE:<n>%%``
anchor resolution off this shared numbering.

Caption-finding order per figure, matching ``latextify/figures/__init__.py``:
    0. An image found inside a table cell (``Figure.in_table`` set) never
       gets this treatment at all -- see below.
    1. If pandoc promoted the image into a native ``Figure`` AST block (this
       happens when the docx uses Word's built-in "Caption" style, or in
       other cases pandoc's docx reader treats as caption-worthy) AND that
       block's own ``.caption`` has non-empty text, use it.
    2. FINDING FROM ITEM 3 (verified): otherwise -- including when pandoc
       promoted a ``Figure`` block but left its ``.caption`` empty -- look at
       the immediately adjacent sibling block (next, then previous) and, if
       its text matches ``^(Figure|Fig\\.?)\\s*(\\d+)\\s*[.:]?``, use the text
       after that label as the caption. A sibling that doesn't match the
       label pattern is treated as ordinary body text, not a caption, to
       avoid false positives.
    3. If nothing matches, the caption is the empty string; the figure
       number still falls back to encounter order.

An image inside a table cell (``Figure.in_table = True``) skips 1-3 entirely
and always gets an empty caption. A cell has no well-defined "adjacent
sibling block" in the same sense a top-level paragraph does -- the next/
previous block in the cell's own content is at best a different cell's
content entirely once row/column structure is accounted for, so guessing
would mean silently mis-attributing another cell's text as this image's
caption. Graceful degradation (no caption) beats a wrong caption; see the
emitter's handling of ``in_table`` figures (bare ``\\includegraphics``, no
``\\caption``, no float wrapper -- a float is not legal LaTeX inside a
``tabular``/``longtable`` cell).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import panflute as pf
import pypandoc

from latextify.model import Figure

_CAPTION_LABEL_RE = re.compile(r"^(?:Figure|Fig\.?)\s*(\d+)\s*[.:]?\s*(.*)$", re.IGNORECASE)


def extract_figures(docx_path: Path | str, media_dir: Path | str) -> tuple[Figure, ...]:
    """Extract embedded media and associate each with a figure number + caption.

    Args:
        docx_path: path to the source .docx manuscript.
        media_dir: directory embedded images are (re-)extracted into; should
            normally be the same directory passed to
            :func:`latextify.ingest.pandoc.convert_docx_to_body`.

    Returns:
        ``Figure`` records in document order, 1-based, with ``source`` left
        at the default ``FigureSource.EMBEDDED`` -- override resolution
        (:mod:`latextify.figures.override`) fills in overrides afterward.
    """
    docx_path = Path(docx_path)
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    ast_json = pypandoc.convert_file(
        str(docx_path),
        to="json",
        format="docx",
        extra_args=["--extract-media", str(media_dir)],
    )
    doc = pf.load(io.StringIO(ast_json))

    blocks = list(doc.content)
    # id() -> index, so an image's top-level ancestor block can be looked up
    # in O(1) instead of a linear list.index() scan (which would also need a
    # custom equality check -- panflute elements don't define __eq__, so
    # list.index() would fall back to identity anyway).
    top_level_index = {id(block): index for index, block in enumerate(blocks)}

    figures: list[Figure] = []
    number = 0

    def collect(elem: pf.Element, doc: pf.Doc) -> None:
        nonlocal number
        if not isinstance(elem, pf.Image):
            return
        number += 1
        in_table = _in_table_cell(elem)
        if in_table:
            caption = ""
        else:
            caption = _caption_for_top_level_image(elem, blocks, top_level_index)
        figures.append(
            Figure(
                number=number,
                caption=caption,
                embedded_path=Path(elem.url),
                in_table=in_table,
            )
        )

    # Full-document walk -- the same traversal mechanism
    # ``latextify.ingest.filters.plant_anchors`` drives its own counting off
    # of, so the two numbering passes can't independently drift out of order
    # (see the module docstring's numbering-contract note). Every ``Image``
    # anywhere in the tree is visited, including one nested inside a table
    # cell.
    doc.walk(collect)
    return tuple(figures)


def _in_table_cell(elem: pf.Element) -> bool:
    """Whether ``elem`` sits inside a table cell.

    Walks the ``.parent`` chain rather than a subtree scan, the same
    technique ``latextify.ingest.filters._is_nested_table`` uses -- valid
    here because this runs from inside a ``doc.walk()`` callback, where every
    visited element already has its ancestor chain populated up to the
    ``Doc`` root.
    """
    ancestor = elem.parent
    while ancestor is not None:
        if isinstance(ancestor, pf.TableCell):
            return True
        ancestor = ancestor.parent
    return False


def _caption_for_top_level_image(
    elem: pf.Element, blocks: list[pf.Element], top_level_index: dict[int, int]
) -> str:
    """Caption for an image NOT inside a table cell: walk up to its
    top-level block (a Para/Plain/Figure directly in ``doc.content``), then
    apply the same Figure-block / adjacent-sibling logic the original
    top-level-only implementation used."""
    ancestor: pf.Element | None = elem
    while ancestor is not None and id(ancestor) not in top_level_index:
        ancestor = ancestor.parent
    if ancestor is None:
        return ""
    index = top_level_index[id(ancestor)]
    caption = _caption_from_figure_block(ancestor)
    if not caption:
        caption = _caption_from_sibling(blocks, index)
    return caption


def _stringify(elements: list[pf.Element]) -> str:
    """Plain text of a list of panflute Block elements."""
    parts: list[str] = []

    def collect(elem: pf.Element, doc: pf.Doc) -> None:
        if isinstance(elem, pf.Str):
            parts.append(elem.text)
        elif isinstance(elem, pf.Space):
            parts.append(" ")

    for element in elements:
        element.walk(collect)
    return "".join(parts).strip()


def _strip_label(text: str) -> str:
    """Drop a leading "Figure N:"/"Fig. N:" label; LaTeX's own \\caption numbers it."""
    match = _CAPTION_LABEL_RE.match(text)
    return match.group(2).strip() if match else text


def _caption_from_figure_block(block: pf.Element) -> str:
    if isinstance(block, pf.Figure):
        return _strip_label(_stringify(list(block.caption.content)))
    return ""


def _caption_from_sibling(blocks: list[pf.Element], index: int) -> str:
    for candidate_index in (index + 1, index - 1):
        if 0 <= candidate_index < len(blocks):
            candidate = blocks[candidate_index]
            if isinstance(candidate, (pf.Para, pf.Plain)):
                text = _stringify([candidate])
                match = _CAPTION_LABEL_RE.match(text)
                if match:
                    return match.group(2).strip()
    return ""
