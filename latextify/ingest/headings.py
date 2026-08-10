"""Recovering section structure from manuscripts that never styled it.

Split out of :mod:`latextify.ingest.filters` (2026-08-10). Two filters, run
first in that module's pipeline:

    promote_pseudo_headings   authors type "III. RESULTS" or an ALL-CAPS line
                              instead of applying Word's Heading style, and
                              pandoc faithfully reproduces a paragraph. Word's
                              ListParagraph headings arrive as single-item
                              enumerate lists. Both are rewritten into real
                              Header nodes so the body gains \\section
                              structure at all -- and so the emitter's
                              reference-list stripping has a
                              \\section{References} to find.
    normalize_headings        shift and clamp levels onto the 1..3 range
                              pandoc's LaTeX writer maps to
                              section/subsection/subsubsection.

Promotion runs first so promoted headers are level-normalized alongside
genuinely styled ones. Both report what they changed as
:class:`~latextify.model.emit.FilterFinding` records rather than acting
silently: a heading LaTeXtify invented is exactly the kind of thing an author
must be able to check.
"""

from __future__ import annotations

import re

import panflute as pf

from latextify.model import FilterFinding

MAX_HEADING_LEVEL = 3

# --- typed (unstyled) section-heading promotion ------------------------------

# A section heading TYPED inline rather than given a Word heading style. These
# recognize the same shapes as
# ``latextify.ingest.metadata_guess._looks_like_section_heading`` (the docx-
# _Para sibling that finds where front matter ends), but on the pandoc-AST side
# and returning the cleaned title + level so a Header can be built -- plus
# arabic-numbered headings the front-matter terminator has no need for. Kept
# parallel rather than shared because the two operate on different data models
# (raw docx paragraph vs pandoc-stringified text) and return different types.
_ROMAN_HEADING_RE = re.compile(r"^[IVXLC]+\.\s+(\S.*)$")
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
_MAX_HEADING_TEXT_LEN = 60


def _section_heading_title(text: str) -> tuple[str, int] | None:
    """Return ``(clean title, level)`` when ``text`` reads as a section heading.

    Recognizes the three shapes real manuscripts type instead of styling:
      * ALL-CAPS -- "INTRODUCTION", "METHODS", "REFERENCES" (level 1)
      * roman-numbered -- "I. Introduction", "II. Methods" (level 1)
      * arabic-numbered -- "1. Introduction" (level 1), "1.1 Methods" (level 2)

    Returns ``None`` for anything longer than :data:`_MAX_HEADING_TEXT_LEN`,
    ending in sentence/label punctuation, or whose title is not itself
    capitalized -- the guards that keep genuine prose and content-list items
    from being mistaken for headings.
    """
    text = text.strip()
    if not text or len(text) > _MAX_HEADING_TEXT_LEN:
        return None
    if text[-1] in ".!?:;,":  # trailing sentence/label punctuation -> not a heading
        return None
    roman = _ROMAN_HEADING_RE.match(text)
    if roman:
        return roman.group(1).strip(), 1
    numbered = _NUMBERED_HEADING_RE.match(text)
    if numbered and numbered.group(2)[:1].isupper():
        level = min(numbered.group(1).count(".") + 1, MAX_HEADING_LEVEL)
        return numbered.group(2).strip(), level
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return text, 1
    return None


def _title_inlines(title: str) -> list[pf.Element]:
    """Build Header inline content from a plain title string (Str + Space)."""
    inlines: list[pf.Element] = []
    for i, word in enumerate(title.split()):
        if i:
            inlines.append(pf.Space())
        inlines.append(pf.Str(word))
    return inlines


def _list_item_heading(item: pf.ListItem) -> tuple[str, int] | None:
    """``(title, level)`` when a list item is a single heading-like paragraph.

    A Word section heading styled as ListParagraph reaches pandoc as a list
    item whose only block is the heading paragraph; the list's own numbering
    (roman/arabic) lives in the marker, so the item TEXT is the bare title and
    only the ALL-CAPS shape typically matches here -- which is exactly what
    keeps a genuine content-list item (mixed-case, sentence) from qualifying.
    """
    blocks = list(item.content)
    if len(blocks) != 1 or not isinstance(blocks[0], (pf.Para, pf.Plain)):
        return None
    return _section_heading_title(pf.stringify(blocks[0]))


def _blocks_to_headers(block: pf.Element) -> list[pf.Header] | None:
    """Headers a top-level ``block`` should become, or ``None`` to leave it.

    Two source shapes: a bare (often bold) paragraph typed as a heading, and a
    ListParagraph-styled heading pandoc read as a list. A list is promoted only
    when EVERY item reads as a heading, so a genuine multi-item content list is
    never disturbed.
    """
    if isinstance(block, (pf.Para, pf.Plain)):
        parsed = _section_heading_title(pf.stringify(block))
        if parsed is None:
            return None
        title, level = parsed
        return [pf.Header(*_title_inlines(title), level=level)]
    if isinstance(block, (pf.OrderedList, pf.BulletList)):
        headers: list[pf.Header] = []
        for item in block.content:
            parsed = _list_item_heading(item)
            if parsed is None:
                return None  # any non-heading item -> genuine list, leave intact
            title, level = parsed
            headers.append(pf.Header(*_title_inlines(title), level=level))
        return headers or None
    return None


def promote_pseudo_headings(doc: pf.Doc) -> tuple[pf.Doc, list[FilterFinding]]:
    """Promote TYPED (unstyled) section headings to real Header nodes.

    Real manuscripts author section headings as bare ALL-CAPS / numbered lines
    with NO Word heading style. pandoc then reads them either as a plain (often
    bold) paragraph or -- when they carry Word's ListParagraph style -- as a
    single-item enumerate list, so the document converts with zero ``\\section``
    commands and (for the list case) the headings render as ``\\begin{enumerate}``
    items. Rewrite each heading-like top-level block to a ``Header`` so the body
    gains real section structure; this also lets the citation stage's
    reference-list stripping (which keys off a ``\\section{References}``-style
    heading) find and drop the typed bibliography (gap 7).

    Only TOP-LEVEL blocks are considered -- a section heading never lives inside
    a table cell or block quote -- and a list is promoted only when EVERY item
    reads as a heading. Mutates ``doc`` in place; also returns it for chaining.
    """
    new_blocks: list[pf.Element] = []
    promoted = 0
    for block in doc.content:
        headers = _blocks_to_headers(block)
        if headers is None:
            new_blocks.append(block)
        else:
            new_blocks.extend(headers)
            promoted += len(headers)
    if promoted:
        doc.content = new_blocks
    findings = (
        [
            FilterFinding(
                message=(
                    f"promoted {promoted} typed section heading(s) to \\section "
                    "(the source styled them as bold/ALL-CAPS or list text, not a "
                    "Word heading style)"
                )
            )
        ]
        if promoted
        else []
    )
    return doc, findings


def normalize_headings(doc: pf.Doc) -> tuple[pf.Doc, list[FilterFinding]]:
    """Shift and clamp Header levels onto the 1..3 range.

    Word documents don't always start heading styles at "Heading 1" (e.g. a
    manuscript that reserves level 1 for the title and starts body sections
    at "Heading 2"), so the minimum level found is shifted to 1. Any level
    that still lands beyond :data:`MAX_HEADING_LEVEL` after the shift is
    clamped to it and recorded as a finding, since ``revtex4-2`` and the
    other journal classes only define down to ``\\subsubsection``.

    Mutates ``doc`` in place; also returns it for chaining.
    """
    findings: list[FilterFinding] = []
    levels: list[int] = []

    def collect(elem: pf.Element, doc: pf.Doc) -> None:
        if isinstance(elem, pf.Header):
            levels.append(elem.level)

    doc.walk(collect)
    if not levels:
        return doc, findings

    shift = min(levels) - 1  # e.g. doc starting at Heading 2 -> shift by 1

    def action(elem: pf.Element, doc: pf.Doc) -> pf.Element | None:
        if isinstance(elem, pf.Header):
            original = elem.level
            level = original - shift
            if level > MAX_HEADING_LEVEL:
                findings.append(
                    FilterFinding(
                        message=(
                            f"heading level {original} exceeds "
                            f"{MAX_HEADING_LEVEL} after normalization; "
                            f"clamped to {MAX_HEADING_LEVEL} "
                            "(\\subsubsection)"
                        )
                    )
                )
                level = MAX_HEADING_LEVEL
            elem.level = max(level, 1)
        return None

    doc = doc.walk(action)
    return doc, findings


# Inline node types that carry real content even though they can stringify to
# "" -- a blank-looking paragraph holding any of these must NOT be dropped.
