"""panflute AST filters applied between pandoc's docx->json and json->latex
passes (see ``latextify.ingest.pandoc.convert_docx_to_body``).

Applied in this order:
    0. :func:`promote_pseudo_headings` -- rewrite section headings the
       manuscript TYPED instead of styling (bare ALL-CAPS / numbered lines, or
       Word ListParagraph headings pandoc read as single-item enumerate lists)
       into real ``Header`` nodes, so the body gains ``\\section`` structure
       (gap 7) and the emitter's reference-list stripping can find a
       ``\\section{References}`` heading. Runs first so the promoted headers
       are level-normalized alongside any genuinely styled ones.
    1. :func:`normalize_headings` -- shift + clamp Header levels onto the
       1..3 range pandoc's LaTeX writer maps to
       ``\\section``/``\\subsection``/``\\subsubsection``.
    2. :func:`strip_word_junk` -- remove empty Span/Div wrappers, empty Str
       runs, and whole blank paragraphs (bookmarks, proofErr marks, a stray
       bold line break / non-breaking space, and similar zero-content
       artifacts docx round-trips can leave behind).
    2b. :func:`associate_table_captions` -- move a stray "Table N:" paragraph
       typed after a table (not styled as a Word caption) into that table's
       ``\\caption{}``. Runs before :func:`plant_anchors` so the caption
       paragraph is still pristine when consumed.
    2c. :func:`allow_slash_line_breaks` -- insert ``\\allowbreak`` after every
       ``/`` in text so a long slash-connected token (a layer stack / chemical
       formula like ``Ta/MnN/CoFeB/TaOx``) can break across lines instead of
       forcing a grotesquely stretched Underfull line in a narrow two-column
       measure. Runs after the text-inspecting structural filters (so their
       heading/caption detection sees intact ``Str`` text) and before
       :func:`normalize_tables` (so table-cell text is covered too).
    3. :func:`plant_anchors` -- replace Image nodes with a raw
       ``%%FIGURE:<n>%%`` LaTeX anchor and any ``Cite`` node with a raw
       ``%%CITE:<idx>%%`` anchor, both numbered in document order, 1-based.
       NOTE: pandoc 3.9's docx reader does NOT emit ``Cite`` nodes from
       Zotero/Mendeley/EndNote citation *field codes* (verified, plan item
       24) -- it emits only the cached display text -- so for field-coded
       citations this ``Cite`` path is dormant and the linkage is handled
       upstream instead by
       :func:`latextify.ingest.citation_sentinels.plant_citation_sentinels`
       (alphanumeric ``ZZLTXCITE<i>ZZ`` sentinels, resolved by the emitter).
       The ``Cite`` path is kept because it is harmless and correct for any
       real ``Cite`` node a future pandoc (or another input path) may yield.
       Anchors are emitted as ``panflute.RawInline(format="latex")`` rather
       than ``Str`` so pandoc's LaTeX writer passes the literal ``%``
       characters through instead of escaping them to ``\\%``. The
       figures/citations stages (items 9, 7) replace these markers with
       resolved content once they have it; anchors that reach
       ``generated/body.tex`` unresolved are a bug in those later stages,
       not here.
    4. :func:`normalize_tables` -- replace EVERY Table node (clean or
       pathological) with a hand-assembled booktabs
       (``\\toprule``/``\\midrule``/``\\bottomrule``, no vertical rules)
       ``RawBlock``. Runs *after* :func:`plant_anchors` so any Image/Cite
       nested inside a table cell (a figure icon in a cell, a citation in a
       caption) has already become a ``%%FIGURE``/``%%CITE`` anchor before the
       cell is rendered to LaTeX text -- if it ran first, anchors inside
       tables would silently never be planted once the Table node is replaced
       by opaque raw text. Tables with a vertically merged cell (Word's
       ``vMerge``, surfaced by pandoc as ``TableCell.rowspan > 1``) or a
       nested table cannot be reconstructed *faithfully* -- see plan item 25
       and the function docstring for why they are instead degraded to a
       structure-losing-but-content-preserving booktabs table with a bold
       in-document note, rather than left for pandoc's own default table
       writer (item 17's original fallback, retired by item 25 -- see below).

ITEM 25 FINDING (pathological-table compile gap, fixed 2026-07-11): item 17's
original fallback left a pathological table's Table AST node completely
untouched, so pandoc's own default LaTeX writer rendered it downstream. That
writer unconditionally emits ``longtable`` + ``\\multirow`` + ``\\real{}``
(from ``calc``) + ``array``'s column specifiers -- packages/macros pandoc
only *defines* in its own ``--standalone`` template preamble, never in
fragment-mode output (which is what this project always requests, since it
supplies its own journal preambles). Verified empirically (real Tectonic
compiles, see tests/test_tables.py's tectonic-marked tests): a manuscript
with a pathological table failed identically in ALL FOUR journals with
``! LaTeX Error: Environment longtable undefined.`` -- item 17's own
compile-harness test had to slice the pathological table's section out of
the body specifically to route around this, which was the tell.

Two fix candidates were evaluated:
    (a) inject pandoc's own longtable-support preamble subset
        (``\\usepackage{longtable,array}``, ``\\newcounter{none}``,
        ``\\usepackage{multirow}``, ``\\usepackage{calc}``) into the
        generated preamble. This DOES preserve real merge fidelity
        (``\\multirow`` renders the actual vMerge) and was verified to
        compile for elsarticle (single-column), sn-jnl (single-column), and
        even revtex4-2's two-column ``reprint`` mode -- REVTeX4-2 turns out
        to carry its own longtable compatibility shim (`Class revtex4-2
        Info: Patching unrecognized longtable package. (Proceeding with
        fingers crossed)`` in the compile log) that happens to make it work.
        It FAILS for ieeetran's genuine two-column ``journal`` mode with
        ``Package longtable Error: longtable not in 1-column mode.`` --
        longtable is fundamentally incompatible with LaTeX's native
        ``twocolumn`` typesetting, and IEEEtran (unlike REVTeX) has no
        compatibility patch for it.
    (b) degrade the pathological table to a best-effort booktabs
        reconstruction that ignores the merge/nesting structure instead of
        attempting it: a vertically merged cell's content is duplicated into
        every row it originally spanned (never blanked -- content must never
        silently vanish); a nested table's content is flattened to
        semicolon/slash-joined plain text (a second ``tabular``/``longtable``
        nested inside a cell is not legal LaTeX regardless of which packages
        are loaded, so this is not merely a style choice). A bold
        ``\\textbf{[table structure simplified -- verify against source]}``
        note is appended immediately after the table.

(b) was chosen, applied UNCONDITIONALLY for every pathological table
regardless of target journal, rather than a per-journal hybrid of (a) for the
three journals it happens to work for and (b) only for ieeetran. Reasoning:
whether (a) compiles is entirely a function of an incidental implementation
detail of the journal's own ``.cls`` file (does it happen to patch
``longtable`` for two-column compatibility, as REVTeX does and IEEEtran does
not) -- encoding that as a manifest flag would mean guessing wrong for any
future two-column journal whose class does NOT carry a similar patch (most
won't), silently reintroducing this exact compile failure for it. (b) alone
needs no extra packages (the existing unconditional ``booktabs`` load from
item 17 is sufficient), never touches ``longtable`` at all, and therefore has
no two-column exposure for ANY journal, present or future. The tradeoff --
losing real merge/nesting structure in the three journals where (a) would
have preserved it -- was judged acceptable given the plan's own framing:
content surviving is the hard requirement, faithful merge structure is not
(readers are pointed at the source .docx via the in-document note instead).

Every function here mutates the ``panflute.Doc`` in place (via ``Doc.walk``)
and also returns it, so callers can chain: ``doc = normalize_headings(doc)``.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import panflute as pf
import pypandoc

from latextify.model import FilterFinding

# A cell counts as "numeric" if, after stripping formatting, its text looks
# like a plain number: optional sign, optional thousands separators, decimal
# part, scientific-notation exponent, and/or a trailing "%". Deliberately
# conservative -- text that merely contains digits (e.g. a sample ID "A2")
# does not match.
_NUMERIC_CELL_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?([eE][+-]?\d+)?%?$")

# pandoc's own Table colspec alignment, when it carries one, always wins over
# the numeric-majority inference below.
_PANDOC_ALIGN_TO_LATEX = {
    "AlignLeft": "l",
    "AlignRight": "r",
    "AlignCenter": "c",
}

# journal preambles only define down to \subsubsection; anything deeper gets
# clamped here rather than silently failing to compile later.
MAX_HEADING_LEVEL = 3


@dataclass
class AnchorCounts:
    """How many of each anchor kind :func:`plant_anchors` planted."""

    figures: int = 0
    citations: int = 0


@dataclass
class FilterResult:
    """Aggregate return value of running all filters in sequence."""

    doc: pf.Doc
    anchors: AnchorCounts
    findings: list[FilterFinding] = field(default_factory=list)


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
_CONTENT_INLINE_TYPES = (pf.Image, pf.Cite, pf.Math, pf.RawInline, pf.Note, pf.Link)


def _is_blank_paragraph(block: pf.Element) -> bool:
    """True for a Para/Plain with no visible content (only spaces/breaks/nbsp).

    Word manuscripts leave empty styled paragraphs behind -- a blank bold line,
    a stray non-breaking space -- which pandoc renders as junk like
    ``\\textbf{\\hfill\\break}`` or a lone ``~`` at the end of the body. Guards
    against a paragraph that looks blank but carries an image, citation, math,
    raw LaTeX, footnote, or link (any of which can stringify to "").
    """
    if not isinstance(block, (pf.Para, pf.Plain)):
        return False
    if pf.stringify(block).strip():
        return False
    has_content = False

    def check(elem: pf.Element, doc: pf.Doc | None = None) -> None:
        nonlocal has_content
        if isinstance(elem, _CONTENT_INLINE_TYPES):
            has_content = True

    block.walk(check)
    return not has_content


def strip_word_junk(doc: pf.Doc) -> pf.Doc:
    """Remove empty Span/Div wrappers, empty Str runs, and blank paragraphs.

    docx round-trips (bookmarks, proofErr marks, tracked-change scaffolding
    pandoc doesn't fully collapse) can leave zero-content elements in the
    AST. They carry no text and pandoc's LaTeX writer would otherwise emit
    stray empty groups/labels for them, so they're dropped outright -- as is a
    whole paragraph that holds nothing but whitespace/line breaks (see
    :func:`_is_blank_paragraph`).

    Mutates ``doc`` in place; also returns it for chaining.
    """

    def action(elem: pf.Element, doc: pf.Doc) -> list | None:
        if isinstance(elem, (pf.Span, pf.Div)) and len(elem.content) == 0:
            return []
        if isinstance(elem, pf.Str) and elem.text == "":
            return []
        if _is_blank_paragraph(elem):
            return []
        return None

    return doc.walk(action)


def plant_anchors(doc: pf.Doc) -> tuple[pf.Doc, AnchorCounts]:
    """Replace Image/Cite nodes with raw LaTeX anchor markers.

    Numbered 1-based in document order: ``%%FIGURE:<n>%%`` for each Image
    encountered, ``%%CITE:<idx>%%`` for each ``Cite``. Mutates ``doc`` in
    place; also returns it (with the counts) for chaining. On pandoc 3.9 the
    ``Cite`` branch does not fire for Zotero/Mendeley field codes (they arrive
    as plain text, handled via citation sentinels -- see the module docstring);
    it is retained for genuine ``Cite`` nodes.
    """
    counts = AnchorCounts()

    def action(elem: pf.Element, doc: pf.Doc) -> pf.RawInline | None:
        if isinstance(elem, pf.Image):
            counts.figures += 1
            return pf.RawInline(f"%%FIGURE:{counts.figures}%%", format="latex")
        if isinstance(elem, pf.Cite):
            counts.citations += 1
            return pf.RawInline(f"%%CITE:{counts.citations}%%", format="latex")
        return None

    doc = doc.walk(action)
    return doc, counts


# ---------------------------------------------------------------------------
# normalize_tables
# ---------------------------------------------------------------------------


def _blocks_to_latex(blocks: list, api_version) -> str:
    """Render a list of panflute Block elements (typically a table cell's
    ``.content``) to a LaTeX text fragment.

    Goes through a real pandoc json->latex call (the same mechanism
    :mod:`latextify.ingest.pandoc` uses for the whole document) rather than
    ``panflute.stringify`` so escaping (``%``, ``&``, ``$``, ...), inline
    markup, and any raw anchors already planted by :func:`plant_anchors`
    survive correctly. ``panflute.convert_text`` is not used directly here
    because its ``input_format="panflute"`` path probes ``pandoc`` on PATH
    for the API version instead of accepting an explicit binary, which fails
    when only pypandoc-binary's vendored pandoc is available (as in this
    project) -- so the Doc-wrap-and-dump is done by hand instead, using the
    already-loaded document's own ``api_version``.
    """
    if not blocks:
        return ""
    sub_doc = pf.Doc(*blocks, api_version=api_version)
    buf = io.StringIO()
    pf.dump(sub_doc, buf)
    tex = pypandoc.convert_text(buf.getvalue(), to="latex", format="json")
    lines = [line for line in tex.replace("\r\n", "\n").split("\n") if line.strip()]
    return " ".join(lines).strip()


def _row_column_slots(row: pf.TableRow) -> list[tuple[int, pf.TableCell]]:
    """(start_column, cell) pairs for a row.

    Assumes ``rowspan == 1`` throughout, which the pathology check below
    already guarantees before this is ever called on a row that reaches
    :func:`_table_to_latex`.
    """
    slots: list[tuple[int, pf.TableCell]] = []
    col = 0
    for cell in row.content:
        slots.append((col, cell))
        col += cell.colspan
    return slots


def _column_alignment_letters(table: pf.Table, data_rows: list[pf.TableRow]) -> list[str]:
    """One LaTeX alignment letter per column, no vertical-rule separators.

    Pandoc's own colspec alignment wins when a column carries one (e.g. an
    explicit alignment from a markdown-table input path); otherwise the
    column is inferred from its data-row content: numeric-majority ->
    right-aligned, else left-aligned. Header/foot rows are excluded from the
    numeric vote; cells spanning more than one column (from a horizontal
    merge) are excluded too since they can't be attributed to a single
    column.
    """
    numeric = [0] * table.cols
    total = [0] * table.cols
    for row in data_rows:
        for col, cell in _row_column_slots(row):
            if cell.colspan != 1 or col >= table.cols:
                continue
            text = pf.stringify(cell).strip()
            if not text:
                continue
            total[col] += 1
            if _NUMERIC_CELL_RE.match(text):
                numeric[col] += 1

    letters = []
    for i in range(table.cols):
        explicit_align = table.colspec[i][0]
        if explicit_align in _PANDOC_ALIGN_TO_LATEX:
            letters.append(_PANDOC_ALIGN_TO_LATEX[explicit_align])
        elif total[i] and numeric[i] * 2 > total[i]:
            letters.append("r")
        else:
            letters.append("l")
    return letters


def _row_to_latex(row: pf.TableRow, api_version) -> str:
    parts = []
    for cell in row.content:
        text = _blocks_to_latex(list(cell.content), api_version)
        if cell.colspan > 1:
            # Horizontal span -> \multicolumn. Use the cell's own alignment
            # if pandoc recorded one, else center (the common convention for
            # a merged header banner).
            align = _PANDOC_ALIGN_TO_LATEX.get(cell.alignment, "c")
            parts.append(f"\\multicolumn{{{cell.colspan}}}{{{align}}}{{{text}}}")
        else:
            parts.append(text)
    return " & ".join(parts) + " \\\\"


def _table_body_rows(table: pf.Table) -> list[pf.TableRow]:
    """Every non-header row: each TableBody's own intermediate head rows
    (rare, but valid pandoc AST) plus its data rows, then any TableFoot
    rows."""
    rows: list[pf.TableRow] = []
    for body in table.content:
        rows.extend(body.head)
        rows.extend(body.content)
    rows.extend(table.foot.content)
    return rows


# In a two-column journal a plain ``table`` float is only ~\columnwidth wide, so
# a table with this many or more columns routinely runs off the page (a real
# manuscript's volume-fraction table overflowed the right margin this way). Such
# a table is instead emitted as a spanning ``table*`` and bounded with the
# shrink-only \resizebox idiom below. Narrow tables (< this many columns) stay a
# plain ``table``.
_WIDE_TABLE_MIN_COLS = 4

# Shrink-only bound for a wide table, using only graphicx (always loaded). Inside
# \resizebox's width argument graphicx exposes \width as the tabular's natural
# width, so the target width is \linewidth ONLY when the table would overflow,
# else its own natural width (an identity scale). A plain ``\resizebox{\textwidth}``
# would instead scale EVERY wide table to exactly \textwidth -- upscaling any
# table narrower than the page and making it look bigger than a single-column
# neighbour (the "Table II is too big / inconsistent with Table I" report). This
# keeps a wide table at its natural size unless it genuinely does not fit, so all
# tables render at a consistent scale. \linewidth (not \textwidth) is correct in
# the spanning ``table*`` (there \linewidth == \textwidth) and stays sane in any
# single-column context.
_SHRINK_TO_FIT_WIDTH = "\\ifdim\\width>\\linewidth\\linewidth\\else\\width\\fi"


def _wrap_table_float(caption_tex: str, tabular_lines: list[str], ncols: int) -> list[str]:
    """Wrap a booktabs ``tabular`` in its float, spanning + bounding wide ones.

    A table with :data:`_WIDE_TABLE_MIN_COLS` or more columns becomes a
    two-column-spanning ``table*`` whose tabular is bounded with the shrink-only
    :data:`_SHRINK_TO_FIT_WIDTH` idiom -- scaled down to ``\\linewidth`` only if
    it would overflow, never scaled up; narrower tables stay a single-column
    ``table`` at their natural size. The caption is kept OUTSIDE the
    ``\\resizebox`` so it is not scaled with the table body.
    """
    wide = ncols >= _WIDE_TABLE_MIN_COLS
    env = "table*" if wide else "table"
    lines = [f"\\begin{{{env}}}[htbp]", "\\centering"]
    if caption_tex:
        lines.append(f"\\caption{{{caption_tex}}}")
    if wide:
        lines.append(f"\\resizebox{{{_SHRINK_TO_FIT_WIDTH}}}{{!}}{{%")
        lines.extend(tabular_lines)
        lines.append("}")
    else:
        lines.extend(tabular_lines)
    lines.append(f"\\end{{{env}}}")
    return lines


def _table_to_latex(table: pf.Table, api_version) -> str:
    """Assemble a ``table``+``tabular`` booktabs float for one clean Table."""
    header_rows = list(table.head.content)
    body_rows = _table_body_rows(table)

    letters = _column_alignment_letters(table, body_rows)
    caption_tex = _blocks_to_latex(list(table.caption.content), api_version)

    tabular = [f"\\begin{{tabular}}{{{''.join(letters)}}}", "\\toprule"]
    for row in header_rows:
        tabular.append(_row_to_latex(row, api_version))
    if header_rows:
        tabular.append("\\midrule")
    for row in body_rows:
        tabular.append(_row_to_latex(row, api_version))
    tabular.append("\\bottomrule")
    tabular.append("\\end{tabular}")
    return "\n".join(_wrap_table_float(caption_tex, tabular, len(letters)))


def _is_nested_table(table: pf.Table) -> bool:
    """Whether ``table`` sits inside a cell of another Table.

    Checked via the ``.parent`` chain rather than a fresh subtree scan so it
    stays correct regardless of ``Doc.walk``'s post-order traversal (a
    nested table's own filter action fires *before* its enclosing table's):
    at the moment a Table's action fires, every ancestor up to the Doc root
    still reflects the pre-filter structure, because only an element's own
    action (never an ancestor's) can replace it.
    """
    ancestor = table.parent
    while ancestor is not None:
        if isinstance(ancestor, pf.Table):
            return True
        ancestor = ancestor.parent
    return False


def _pathology_reason(table: pf.Table) -> str | None:
    """Why ``table`` needs the degraded reconstruction path, or ``None`` if clean.

    Two disqualifying conditions, per plan item 17: a vertically merged cell
    (Word's ``vMerge``, surfaced by pandoc as ``TableCell.rowspan > 1``
    anywhere in the table), or a nested table. Neither has a direct booktabs
    equivalent (rowspan needs ``\\multirow``; a nested table can't become a
    second ``tabular``/``longtable`` inside a cell -- that's not legal LaTeX
    regardless of which packages are loaded) -- so a *faithful*
    reconstruction is unsafe to attempt. :func:`_degraded_table_to_latex`
    handles both by discarding the merge/nesting structure while keeping
    every piece of cell content (see plan item 25 and the module docstring's
    fix (a)-vs-(b) writeup for why item 17's original "leave it for pandoc's
    default writer" fallback was retired).
    """
    found_rowspan = False
    found_nested = False

    def check(elem: pf.Element, doc: pf.Doc) -> None:
        nonlocal found_rowspan, found_nested
        if isinstance(elem, pf.TableCell) and elem.rowspan > 1:
            found_rowspan = True
        if isinstance(elem, pf.Table) and elem is not table:
            found_nested = True

    table.walk(check)
    if found_nested:
        return "contains a nested table"
    if found_rowspan:
        return "has a vertically merged cell (vMerge)"
    return None


# ---------------------------------------------------------------------------
# Degraded reconstruction for pathological tables (plan item 25)
# ---------------------------------------------------------------------------

_DEGRADED_TABLE_NOTE = "[table structure simplified -- verify against source]"


def _flatten_nested_table_text(table: pf.Table) -> str:
    """Plain-text flatten of a nested table: cells ``"; "``-joined, rows
    ``" / "``-joined.

    Used only when a pathological table's cell itself contains a nested
    Table AST node -- a second ``tabular``/``longtable`` inside a cell is not
    legal LaTeX, so the nested table can never be rendered as a table again
    here; every leaf of its content is kept as plain text instead (structure
    lost, content preserved, matching the vMerge-duplication degrade below).
    ``pf.stringify`` recurses through arbitrarily nested content on its own,
    so a table nested inside *this* nested table's own cells is handled for
    free without any extra recursion here.
    """
    rows = list(table.head.content)
    for body in table.content:
        rows.extend(body.head)
        rows.extend(body.content)
    rows.extend(table.foot.content)

    row_texts = []
    for row in rows:
        cell_texts = [pf.stringify(cell).strip() for cell in row.content]
        joined = "; ".join(text for text in cell_texts if text)
        if joined:
            row_texts.append(joined)
    return " / ".join(row_texts)


def _degraded_blocks_to_latex(blocks: list, api_version) -> str:
    """Like :func:`_blocks_to_latex`, but first replaces any nested Table
    descendant with flattened plain text (see
    :func:`_flatten_nested_table_text`).

    Only used by the degraded-reconstruction path: the clean-table path
    (:func:`_blocks_to_latex`) never encounters a nested table to begin with
    -- :func:`_pathology_reason` already excludes any table that has one.
    """
    if not blocks:
        return ""
    sub_doc = pf.Doc(*blocks, api_version=api_version)

    def flatten(elem: pf.Element, doc: pf.Doc) -> pf.Para | None:
        if isinstance(elem, pf.Table):
            return pf.Para(pf.Str(_flatten_nested_table_text(elem)))
        return None

    sub_doc = sub_doc.walk(flatten)
    buf = io.StringIO()
    pf.dump(sub_doc, buf)
    tex = pypandoc.convert_text(buf.getvalue(), to="latex", format="json")
    lines = [line for line in tex.replace("\r\n", "\n").split("\n") if line.strip()]
    return " ".join(lines).strip()


@dataclass
class _GridSlot:
    """One column-aligned slot in a degraded table's row expansion.

    ``cell`` is the real :class:`panflute.TableCell` that starts here, or
    ``None`` if this slot is a carried-over duplicate of a vertically merged
    cell from an earlier row (in which case ``carried_from`` names the
    original cell whose content is being duplicated, never re-rendered).
    """

    col: int
    colspan: int
    cell: pf.TableCell | None
    carried_from: pf.TableCell | None = None


def _expand_grid_rows(rows: list[pf.TableRow], ncols: int) -> list[list[_GridSlot]]:
    """Expand each row to ``ncols`` worth of slots, carrying a rowspan cell's
    reference into every row it originally covered.

    Word's vMerge means pandoc's AST omits a cell entirely at any row/column
    position the merge covers past the first, so a naive left-to-right walk
    of ``row.content`` desyncs from the true column index after the first
    vMerge (the next real cell in a covered row actually belongs to a later
    column than its position in ``row.content`` suggests). This keeps
    "column N" meaning the same logical column across every row by tracking,
    per column, how many more rows a rowspan cell still covers and
    re-emitting a reference to it (never a fresh copy of the content --
    callers duplicate the referenced cell's own rendered text) at each of
    those rows.
    """
    pending: dict[int, tuple[pf.TableCell, int]] = {}
    expanded: list[list[_GridSlot]] = []
    for row in rows:
        cells = iter(row.content)
        col = 0
        slots: list[_GridSlot] = []
        while col < ncols:
            if col in pending:
                source, remaining = pending[col]
                slots.append(
                    _GridSlot(col=col, colspan=source.colspan, cell=None, carried_from=source)
                )
                remaining -= 1
                if remaining > 0:
                    pending[col] = (source, remaining)
                else:
                    del pending[col]
                col += source.colspan
                continue
            cell = next(cells, None)
            if cell is None:
                break  # malformed/shorter-than-expected row -- stop, never loop forever
            if cell.rowspan > 1:
                pending[col] = (cell, cell.rowspan - 1)
            slots.append(_GridSlot(col=col, colspan=cell.colspan, cell=cell))
            col += cell.colspan
        expanded.append(slots)
    return expanded


def _degraded_column_alignment_letters(table: pf.Table, body_rows: list[pf.TableRow]) -> list[str]:
    """Like :func:`_column_alignment_letters`, but grid-aware (via
    :func:`_expand_grid_rows`) so the numeric-vote stays attributed to the
    correct column even after a vertical merge desyncs raw cell order from
    column index (see that function's docstring)."""
    numeric = [0] * table.cols
    total = [0] * table.cols
    for slots in _expand_grid_rows(body_rows, table.cols):
        for slot in slots:
            if slot.colspan != 1 or slot.col >= table.cols:
                continue
            source = slot.cell if slot.cell is not None else slot.carried_from
            text = pf.stringify(source).strip()
            if not text:
                continue
            total[slot.col] += 1
            if _NUMERIC_CELL_RE.match(text):
                numeric[slot.col] += 1

    letters = []
    for i in range(table.cols):
        explicit_align = table.colspec[i][0]
        if explicit_align in _PANDOC_ALIGN_TO_LATEX:
            letters.append(_PANDOC_ALIGN_TO_LATEX[explicit_align])
        elif total[i] and numeric[i] * 2 > total[i]:
            letters.append("r")
        else:
            letters.append("l")
    return letters


def _degraded_row_to_latex(
    slots: list[_GridSlot], api_version, cache: dict[int, str]
) -> str:
    """Render one row's grid slots to a LaTeX table row, memoizing each real
    cell's rendered text by ``id()`` so a vertically merged cell is only run
    through pandoc once even though its text is duplicated into every row it
    spans."""
    parts = []
    for slot in slots:
        source = slot.cell if slot.cell is not None else slot.carried_from
        key = id(source)
        if key not in cache:
            cache[key] = _degraded_blocks_to_latex(list(source.content), api_version)
        text = cache[key]
        if slot.colspan > 1:
            align = _PANDOC_ALIGN_TO_LATEX.get(source.alignment, "c")
            parts.append(f"\\multicolumn{{{slot.colspan}}}{{{align}}}{{{text}}}")
        else:
            parts.append(text)
    return " & ".join(parts) + " \\\\"


def _degraded_table_to_latex(table: pf.Table, api_version) -> str:
    """Best-effort booktabs reconstruction of a pathological table (item 25).

    Ignores the merge/nesting structure that made ``table`` pathological
    instead of leaving it for pandoc's own default (fragment-mode-incompatible
    -- see the module docstring) table writer:

        * a vertically merged cell's content is duplicated into every row it
          originally spanned, instead of ``\\multirow`` (see
          :func:`_expand_grid_rows`/:func:`_degraded_row_to_latex`);
        * a nested table's content is flattened to plain text instead of a
          second (illegal) nested ``tabular``/``longtable`` (see
          :func:`_degraded_blocks_to_latex`).

    No cell content is ever dropped -- only the merge/nesting STRUCTURE is --
    and a bold in-document note (:data:`_DEGRADED_TABLE_NOTE`) is appended
    immediately after the table so a reader (and the report) knows to check
    the source .docx for the original structure. Needs nothing beyond
    ``booktabs`` (already unconditional in every journal manifest since item
    17): no ``longtable``, ``multirow``, ``array``, or ``calc``, so this has
    no two-column compile exposure in any journal (see the module docstring's
    fix (a)-vs-(b) writeup).
    """
    header_rows = list(table.head.content)
    body_rows = _table_body_rows(table)

    letters = _degraded_column_alignment_letters(table, body_rows)
    caption_tex = _degraded_blocks_to_latex(list(table.caption.content), api_version)

    cache: dict[int, str] = {}
    tabular = [f"\\begin{{tabular}}{{{''.join(letters)}}}", "\\toprule"]
    for slots in _expand_grid_rows(header_rows, table.cols):
        tabular.append(_degraded_row_to_latex(slots, api_version, cache))
    if header_rows:
        tabular.append("\\midrule")
    for slots in _expand_grid_rows(body_rows, table.cols):
        tabular.append(_degraded_row_to_latex(slots, api_version, cache))
    tabular.append("\\bottomrule")
    tabular.append("\\end{tabular}")
    lines = _wrap_table_float(caption_tex, tabular, len(letters))
    lines.append("")
    lines.append(f"\\noindent\\textbf{{{_DEGRADED_TABLE_NOTE}}}")
    return "\n".join(lines)


# A "Table N:" / "Table N." caption LABEL that leads a stray caption paragraph.
# The numeral (roman or arabic) must be a complete token (the lookahead) so
# "Table Index of ..." is not misread as table "I". revtex renumbers, so only
# the text AFTER the label (group "rest") is kept.
_TABLE_CAPTION_LABEL_RE = re.compile(
    r"^Table\s+(?:[IVXLC]+|\d+)(?=[\s.:])\s*[.:]?\s*(?P<rest>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def associate_table_captions(doc: pf.Doc) -> tuple[pf.Doc, list[FilterFinding]]:
    """Attach a stray "Table N:" caption paragraph to its table.

    When a manuscript types a table's caption as an ordinary paragraph right
    after the table (not Word's Caption style), pandoc leaves the table's own
    ``.caption`` empty and the "Table N: ..." text as a separate body block --
    so it renders as loose prose and the table shows no caption. For a top-level
    table with an empty caption whose immediately-following block is such a
    paragraph, move that paragraph's text (minus the "Table N:" label) into the
    table's caption and drop the paragraph. Mirrors the figure sibling-caption
    search (:mod:`latextify.figures.extract`). Runs before :func:`plant_anchors`
    so the caption paragraph is still pristine. Mutates ``doc``; also returns it.
    """
    blocks = list(doc.content)
    new_blocks: list[pf.Element] = []
    findings: list[FilterFinding] = []
    skip_next = False
    for i, block in enumerate(blocks):
        if skip_next:
            skip_next = False
            continue
        if isinstance(block, pf.Table) and not pf.stringify(block.caption).strip():
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if isinstance(nxt, (pf.Para, pf.Plain)):
                match = _TABLE_CAPTION_LABEL_RE.match(pf.stringify(nxt).strip())
                if match:
                    block.caption = pf.Caption(pf.Plain(*_title_inlines(match.group("rest"))))
                    skip_next = True
                    findings.append(
                        FilterFinding(
                            message=(
                                "associated a 'Table N:' caption paragraph with its "
                                "table (the source did not use Word's Caption style)"
                            )
                        )
                    )
        new_blocks.append(block)
    doc.content = new_blocks
    return doc, findings


def normalize_tables(doc: pf.Doc) -> tuple[pf.Doc, list[FilterFinding]]:
    """Replace every Table node with hand-assembled booktabs LaTeX.

    "Clean" tables (no vertically merged cell, no nested table anywhere in
    them -- see :func:`_pathology_reason`) are replaced outright by a
    ``RawBlock`` containing a ``table``/``tabular`` float using
    ``\\toprule``/``\\midrule``/``\\bottomrule`` and no vertical rules;
    columns are right-aligned when numeric-majority, else left-aligned
    (pandoc's own colspec alignment wins when present); a horizontal span
    (Word's ``gridSpan``) becomes ``\\multicolumn``.

    A table that fails the pathology check is reconstructed by
    :func:`_degraded_table_to_latex` instead -- a booktabs table that
    discards the merge/nesting structure but keeps every piece of cell
    content, plus a bold in-document note -- and a
    :class:`~latextify.model.FilterFinding` is recorded naming the table by
    its 1-based document-order index, e.g. ``"table 2: has a vertically
    merged cell (vMerge); merge/nesting structure could not be safely
    reconstructed -- emitted as a simplified table with merged cells
    duplicated and a bold in-document note; verify the structure against the
    source document"``. See plan item 25 and the module docstring for why
    this replaced item 17's original "leave it for pandoc's own default table
    writer" fallback (that output doesn't compile in fragment mode).

    Tables nested inside another table's cell are never independently
    counted, transformed, or reported on: the nested table already makes its
    *enclosing* table pathological (see :func:`_pathology_reason`'s nested-
    table check), and the enclosing table's own degraded reconstruction is
    what flattens it (via :func:`_degraded_blocks_to_latex`) -- the nested
    Table AST node must still be intact when the enclosing table's action
    fires, which ``Doc.walk``'s post-order traversal guarantees (a nested
    table's own action always fires first and is a no-op here).

    Mutates ``doc`` in place; also returns it (with findings) for chaining.
    """
    findings: list[FilterFinding] = []
    counter = {"n": 0}

    def action(elem: pf.Element, doc: pf.Doc) -> pf.RawBlock | None:
        if not isinstance(elem, pf.Table):
            return None
        if _is_nested_table(elem):
            return None

        counter["n"] += 1
        index = counter["n"]

        reason = _pathology_reason(elem)
        if reason is not None:
            findings.append(
                FilterFinding(
                    message=(
                        f"table {index}: {reason}; merge/nesting structure "
                        "could not be safely reconstructed -- emitted as a "
                        "simplified table with merged cells duplicated and a "
                        "bold in-document note; verify the structure against "
                        "the source document"
                    )
                )
            )
            tex = _degraded_table_to_latex(elem, doc.api_version)
            return pf.RawBlock(tex, format="latex")

        tex = _table_to_latex(elem, doc.api_version)
        return pf.RawBlock(tex, format="latex")

    doc = doc.walk(action)
    return doc, findings


def allow_slash_line_breaks(doc: pf.Doc) -> pf.Doc:
    r"""Permit a line break after every ``/`` in body text.

    LaTeX sets no breakpoint after ``/``, so a slash-connected run typeset as a
    single token -- a layer stack / chemical formula like
    ``Ta(10)/MnN(t)/CoFeB(t)/TaOx(2.5)``, or a plain ``and/or`` -- is one
    unbreakable "word". In a journal's narrow two-column measure TeX can neither
    fit it on the current line nor split it, so it drops the whole token to the
    next line and stretches the previous line's inter-word glue to justify it
    (an ``Underfull \hbox`` at badness 10000 -- the grotesque word gaps seen at
    the start of the MnN paper's Methods section). Splitting each ``Str`` on
    ``/`` and inserting a raw ``\allowbreak{}`` after the slash lets TeX break
    the stack across lines; it only *permits* a break, so any run that already
    fits is visually unchanged.

    Only ``Str`` (text) nodes are rewritten. File paths and URLs live in
    ``Image``/``Link`` ``.url`` slots (never a ``Str``), so
    ``\includegraphics{figures/fig1.png}`` and ``\href`` targets are never
    split. Mutates ``doc`` in place; also returns it for chaining.
    """

    def action(elem: pf.Element, doc: pf.Doc) -> list | None:
        if not isinstance(elem, pf.Str) or "/" not in elem.text:
            return None
        parts = elem.text.split("/")
        out: list[pf.Element] = []
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            text = part if last else part + "/"
            if text:
                out.append(pf.Str(text))
            if not last:
                out.append(pf.RawInline("\\allowbreak{}", format="latex"))
        return out

    return doc.walk(action)


def apply_all(doc: pf.Doc) -> FilterResult:
    """Run all filters in the fixed order documented above."""
    doc, promo_findings = promote_pseudo_headings(doc)
    doc, heading_findings = normalize_headings(doc)
    doc = strip_word_junk(doc)
    doc, caption_findings = associate_table_captions(doc)
    doc = allow_slash_line_breaks(doc)
    doc, anchors = plant_anchors(doc)
    doc, table_findings = normalize_tables(doc)
    return FilterResult(
        doc=doc,
        anchors=anchors,
        findings=promo_findings + heading_findings + caption_findings + table_findings,
    )
