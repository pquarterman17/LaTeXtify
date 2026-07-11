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

Numbering contract: figures are numbered by encounter order among the
document's top-level blocks, 1-based. This must match
``latextify.ingest.filters.plant_anchors``, which numbers ``Image`` nodes by
a full-document ``.walk()`` in document order -- for the flat paragraph
structure real manuscripts use (one image per paragraph, no images nested
inside other block containers), both traversals visit images in the same
order and therefore agree on numbers. The emitter (item 5) keys
``%%FIGURE:<n>%%`` anchor resolution off this shared numbering.

Caption-finding order per figure, matching ``latextify/figures/__init__.py``:
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
    figures: list[Figure] = []
    number = 0
    for index, block in enumerate(blocks):
        image = _find_image(block)
        if image is None:
            continue
        number += 1
        caption = _caption_from_figure_block(block)
        if not caption:
            caption = _caption_from_sibling(blocks, index)
        figures.append(
            Figure(
                number=number,
                caption=caption,
                embedded_path=Path(image.url),
            )
        )
    return tuple(figures)


def _find_image(block: pf.Element) -> pf.Image | None:
    """Return the ``Image`` within a Para/Plain/Figure block, if any."""
    if not isinstance(block, (pf.Para, pf.Plain, pf.Figure)):
        return None

    found: list[pf.Image] = []

    def collect(elem: pf.Element, doc: pf.Doc) -> None:
        if isinstance(elem, pf.Image):
            found.append(elem)

    block.walk(collect)
    return found[0] if found else None


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
