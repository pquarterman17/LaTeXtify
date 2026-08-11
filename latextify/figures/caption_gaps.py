"""Detect figures the manuscript captions but has no image for.

A manuscript that says "Figure 3: ..." with the figure supplied separately --
sent alongside the .docx rather than pasted into it -- produces a caption with
no image behind it. That is common in submissions where the figures are
delivered as separate high-resolution files.

The failure it causes is silent and serious. Pandoc's docx reader pairs a
Caption-styled paragraph with the image ADJACENT to it, so a caption-only
"Figure 3" sitting just above figure 4's image gets bound to that image.
VERIFIED (2026-08-10) on a generated fixture: a manuscript captioning figures
1-4 with no image for 3 emits three figures, the third being figure 4's image
carrying figure 3's caption, while "Figure 4: The fourth figure." is left
behind in the body as an ordinary paragraph. Nothing warned. A paper shipped
that way has a mislabelled figure and a stray line of text.

LaTeXtify cannot fix pandoc's pairing from the outside, but it can see the
discrepancy, because the caption states its own number: the manuscript claims
figures 1, 2, 3, 4 while only three images exist. :func:`caption_gaps`
reports exactly that, and the emitter turns each gap into a warning naming
the remedy (drop the missing figure in as ``figures/fig<N>.<ext>``, which the
existing folder-convention override then resolves).

This reads the .docx directly rather than the pandoc AST on purpose: by the
time pandoc has built the AST it has already consumed the caption paragraphs
into Figure blocks, which is the very mis-binding being detected.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

#: WordprocessingML namespace.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: The same caption label shape :mod:`latextify.figures.extract` matches, kept
#: deliberately in sync with it -- a caption this module does not recognise is
#: a gap it cannot report. Group 1 is the stated number.
_CAPTION_LABEL_RE = re.compile(
    r"^(?:Supp(?:lement(?:al|ary)?|l)?\.?\s+)?(?:Figure|Fig\.?)\s*S?\s*(\d+)\s*[.:]?\s*(.*)$",
    re.IGNORECASE,
)


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{_W}t")).strip()


def _has_image(paragraph: ElementTree.Element) -> bool:
    """True when this paragraph carries an embedded drawing."""
    return next(paragraph.iter(f"{_W}drawing"), None) is not None or (
        next(paragraph.iter(f"{_W}pict"), None) is not None  # legacy VML shape
    )


def _caption_paragraphs(docx_path: Path | str) -> list[tuple[int, bool]]:
    """``(stated number, an image sits adjacent)`` for each caption paragraph.

    Adjacency is checked against the paragraph itself and its immediate
    neighbours in document order, which is exactly the window Word's caption
    convention (and pandoc's pairing) uses. Reading the .docx rather than the
    AST is the point: pandoc has already consumed captions into Figure blocks
    by then, which is the mis-binding being detected.
    """
    path = Path(docx_path)
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return []

    body = next(root.iter(f"{_W}body"), None)
    if body is None:
        return []
    paragraphs = [node for node in body.iter(f"{_W}p")]
    found: list[tuple[int, bool]] = []
    for index, paragraph in enumerate(paragraphs):
        match = _CAPTION_LABEL_RE.match(_paragraph_text(paragraph))
        if not match:
            continue
        window = paragraphs[max(0, index - 1) : index + 2]
        found.append((int(match.group(1)), any(_has_image(p) for p in window)))
    return found


def stated_caption_numbers(docx_path: Path | str) -> list[int]:
    """Every figure number the manuscript's caption paragraphs claim, in order.

    Duplicates are preserved: two paragraphs both labelled "Figure 2" is
    itself a manuscript error worth seeing rather than silently collapsing.
    Returns an empty list for a file that cannot be read as a .docx -- this is
    an advisory check and must never be the thing that fails a conversion.
    """
    path = Path(docx_path)
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return []

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    numbers: list[int] = []
    for paragraph in root.iter(f"{_W}p"):
        match = _CAPTION_LABEL_RE.match(_paragraph_text(paragraph))
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def caption_gaps(docx_path: Path | str, image_count: int) -> list[int]:
    """Figure numbers the manuscript captions but has no image for.

    The number reported is the one whose OWN caption paragraph has no image
    beside it -- not merely the tail of the sequence. That distinction is the
    difference between an actionable message and a misleading one: in the
    verified fixture (captions 1-4, no image for 3) a count-based check names
    Figure 4, because pandoc shifted caption 3 onto figure 4's image and left
    caption 4 orphaned. Telling the author to supply ``fig4`` when ``fig3`` is
    what is missing sends them after the wrong file.

    ``image_count`` is used only as a corroborating guard: if as many images
    were extracted as captions were written, the document is internally
    consistent and nothing is reported even if the adjacency scan is unsure.

    Returns the missing numbers in ascending order, or an empty list when the
    captions and images agree (including the common case of no captions at
    all, where there is nothing to check against).
    """
    captions = _caption_paragraphs(docx_path)
    if not captions:
        return []
    stated = sorted({number for number, _ in captions})
    # Only a contiguous 1..N caption scheme can be checked this way; an author
    # numbering figures 2, 5, 9 is doing something this heuristic should keep
    # its hands off rather than flood with false gaps.
    if stated != list(range(1, len(stated) + 1)):
        return []
    if len(stated) <= image_count:
        return []
    return sorted({number for number, adjacent in captions if not adjacent})


def gap_warning(number: int, prefix: str = "") -> str:
    """The actionable message for one caption gap."""
    return (
        f"the manuscript captions Figure {number} but no image was found for it -- it was "
        "likely supplied as a separate file rather than pasted into the document. Word "
        "binds a caption to the image next to it, so this figure's caption and every "
        "caption after it may be attached to the WRONG image, and the last caption may be "
        f"left in the text as an ordinary paragraph. Drop the missing figure in as "
        f"figures/fig{prefix}{number}.<pdf|png|jpg|eps|svg> beside the manuscript (or list "
        "it in figures.yaml) and re-run."
    )
