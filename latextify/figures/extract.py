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
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import panflute as pf
import pypandoc

from latextify.figures.crop import attach_crops, image_crops
from latextify.ingest.formats import pandoc_format_for
from latextify.model import Figure

# A figure caption label: an optional "Supplemental/Supplementary" prefix, then
# "Figure"/"Fig.", then the number (which may itself carry an "S" prefix, e.g.
# "Fig. S1"). Group 1 is the bare digits, group 2 the caption body. Supplement
# manuscripts commonly label captions "Supplemental Fig. N:" -- without the
# prefix branch the whole caption is treated as ordinary text and dropped.
_CAPTION_LABEL_RE = re.compile(
    r"^(?:Supp(?:lement(?:al|ary)?|l)?\.?\s+)?(?:Figure|Fig\.?)\s*S?\s*(\d+)\s*[.:]?\s*(.*)$",
    re.IGNORECASE,
)

# WordprocessingML namespace; a floating text box's text lives in
# <w:txbxContent><w:p>...<w:t>text</w:t>... anchored to the body.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


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

    # Format routing (GUI_OPTIONS_FORMATS_PLAN item 9): pandoc reads
    # .odt/.rtf/.md natively, and this whole function's Image-node walk is
    # already format-agnostic -- only _textbox_captions below is docx-
    # specific, and it already degrades to {} for any non-docx file (a bad/
    # foreign zip, or a valid zip with no word/document.xml member). A
    # format pandoc's own reader can't extract images from (e.g. a bare RTF
    # with no Image AST nodes) naturally yields zero figures here too --
    # graceful, not a special case.
    ast_json = pypandoc.convert_file(
        str(docx_path),
        to="json",
        format=pandoc_format_for(docx_path),
        # --resource-path: markdown's externally-referenced images (relative
        # to the manuscript, not pandoc's own cwd) otherwise fail to resolve.
        extra_args=[
            "--extract-media", str(media_dir),
            "--resource-path", str(docx_path.parent),
        ],
    )
    doc = pf.load(io.StringIO(ast_json))
    textbox_captions = _textbox_captions(docx_path)

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
            # Fallback for captions authored as floating TEXT BOXES: pandoc
            # drops text-box content, so the AST search above finds nothing.
            # Match this figure's number to a "FIG. N: ..." text box.
            if not caption:
                caption = textbox_captions.get(number, "")
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
    # Attach Word display crops (a:srcRect) so the emitter can trim each
    # cropped image to its visible region -- otherwise the full original
    # pixels (everything the author cropped OUT) leak into the output.
    return attach_crops(tuple(figures), image_crops(docx_path))


def looks_like_figure_caption(text: str) -> bool:
    """True when ``text`` reads as a figure-caption label line.

    Recognises ``Fig. N`` / ``Figure N`` / ``Supplemental Fig. N`` and the
    ``S``-numbered variants. Used both to recover text-box captions here and to
    stop preflight from flagging a caption text box as "content will be dropped"
    -- those are now recovered, not dropped.
    """
    return _CAPTION_LABEL_RE.match(text.strip()) is not None


def _textbox_captions(docx_path: Path) -> dict[int, str]:
    """Map figure number -> caption text for captions authored as text boxes.

    Word manuscripts commonly float each figure's caption in a text box
    anchored beside the image rather than as an inline paragraph. Pandoc drops
    text-box content entirely, so those captions never reach the AST. This
    reads ``word/document.xml`` directly, pulls the text of every ``w:txbx
    Content`` box, and keys any box whose text reads as ``FIG. N: ...`` /
    ``Figure N ...`` by its label number ``N`` (with the label stripped, since
    LaTeX renumbers). Boxes are typically duplicated (a DrawingML box plus a
    VML fallback with identical text), so the first wins. A docx that cannot be
    opened, or has no such text boxes, yields an empty map -- a pure fallback,
    never an error.
    """
    captions: dict[int, str] = {}
    try:
        with zipfile.ZipFile(docx_path) as archive:
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return captions
    for box in root.iter(_W + "txbxContent"):
        text = "".join(node.text or "" for node in box.iter(_W + "t")).strip()
        match = _CAPTION_LABEL_RE.match(text)
        if match:
            number = int(match.group(1))
            body = match.group(2).strip()
            if body and number not in captions:
                captions[number] = body
    return captions


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
