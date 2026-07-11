"""panflute AST filters applied between pandoc's docx->json and json->latex
passes (see ``latextify.ingest.pandoc.convert_docx_to_body``).

Applied in this order:
    1. :func:`normalize_headings` -- shift + clamp Header levels onto the
       1..3 range pandoc's LaTeX writer maps to
       ``\\section``/``\\subsection``/``\\subsubsection``.
    2. :func:`strip_word_junk` -- remove empty Span/Div wrappers and empty
       Str runs (bookmarks, proofErr marks, and similar zero-content
       artifacts docx round-trips can leave behind).
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
    4. :func:`normalize_tables` -- replace "clean" Table nodes with a
       hand-assembled booktabs (``\\toprule``/``\\midrule``/``\\bottomrule``,
       no vertical rules) ``RawBlock``. Runs *after* :func:`plant_anchors` so
       any Image/Cite nested inside a table cell (a figure icon in a cell, a
       citation in a caption) has already become a ``%%FIGURE``/``%%CITE``
       anchor before the cell is rendered to LaTeX text -- if it ran first,
       anchors inside tables would silently never be planted once the Table
       node is replaced by opaque raw text. Tables with a vertically merged
       cell (Word's ``vMerge``, surfaced by pandoc as ``TableCell.rowspan >
       1``) or a nested table are never reconstructed -- see the function
       docstring for the fallback behavior.

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
    """Aggregate return value of running all three filters in sequence."""

    doc: pf.Doc
    anchors: AnchorCounts
    findings: list[FilterFinding] = field(default_factory=list)


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


def strip_word_junk(doc: pf.Doc) -> pf.Doc:
    """Remove empty Span/Div wrappers and empty Str runs.

    docx round-trips (bookmarks, proofErr marks, tracked-change scaffolding
    pandoc doesn't fully collapse) can leave zero-content elements in the
    AST. They carry no text and pandoc's LaTeX writer would otherwise emit
    stray empty groups/labels for them, so they're dropped outright.

    Mutates ``doc`` in place; also returns it for chaining.
    """

    def action(elem: pf.Element, doc: pf.Doc) -> list | None:
        if isinstance(elem, (pf.Span, pf.Div)) and len(elem.content) == 0:
            return []
        if isinstance(elem, pf.Str) and elem.text == "":
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


def _table_to_latex(table: pf.Table, api_version) -> str:
    """Assemble a ``table``+``tabular`` booktabs float for one clean Table."""
    header_rows = list(table.head.content)
    body_rows = _table_body_rows(table)

    letters = _column_alignment_letters(table, body_rows)
    caption_tex = _blocks_to_latex(list(table.caption.content), api_version)

    lines = ["\\begin{table}[htbp]", "\\centering"]
    if caption_tex:
        lines.append(f"\\caption{{{caption_tex}}}")
    lines.append(f"\\begin{{tabular}}{{{''.join(letters)}}}")
    lines.append("\\toprule")
    for row in header_rows:
        lines.append(_row_to_latex(row, api_version))
    if header_rows:
        lines.append("\\midrule")
    for row in body_rows:
        lines.append(_row_to_latex(row, api_version))
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    return "\n".join(lines)


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
    """Why ``table`` must NOT be reconstructed, or ``None`` if it's clean.

    Two disqualifying conditions, per plan item 17: a vertically merged cell
    (Word's ``vMerge``, surfaced by pandoc as ``TableCell.rowspan > 1``
    anywhere in the table), or a nested table. Either makes booktabs
    reconstruction unsafe to attempt (rowspan has no direct booktabs
    equivalent without \\multirow, which this filter deliberately does not
    attempt to reconstruct; a nested table can't be flattened into a single
    ``tabular`` without losing structure) -- so the whole table is left
    untouched for pandoc's own default table writer to render instead of
    risking silent corruption.
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


def normalize_tables(doc: pf.Doc) -> tuple[pf.Doc, list[FilterFinding]]:
    """Replace clean Table nodes with hand-assembled booktabs LaTeX.

    "Clean" means: no vertically merged cell and no nested table anywhere in
    it (see :func:`_pathology_reason`). A clean table is replaced outright by
    a ``RawBlock`` containing a ``table``/``tabular`` float using
    ``\\toprule``/``\\midrule``/``\\bottomrule`` and no vertical rules;
    columns are right-aligned when numeric-majority, else left-aligned
    (pandoc's own colspec alignment wins when present); a horizontal span
    (Word's ``gridSpan``) becomes ``\\multicolumn``.

    A table that fails the pathology check is left completely untouched (so
    pandoc's own default table writer renders it downstream) and a
    :class:`~latextify.model.FilterFinding` is recorded naming the table by
    its 1-based document-order index, e.g. ``"table 2: has a vertically
    merged cell (vMerge); not reconstructed -- falling back to pandoc's
    default table rendering"``. Never attempts a partial reconstruction --
    the whole table, unresolved-anchors-and-all, is pandoc's problem again.

    Tables nested inside another table's cell are never independently
    counted, transformed, or reported on: the nested table already makes its
    *enclosing* table pathological (see :func:`_pathology_reason`'s nested-
    table check), and turning the inner one into a raw ``table`` float would
    plant an illegal float environment inside the outer table's cell content
    once the outer table falls back to pandoc's normal (non-raw-block)
    rendering.

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
                        f"table {index}: {reason}; not reconstructed -- "
                        "falling back to pandoc's default table rendering"
                    )
                )
            )
            return None

        tex = _table_to_latex(elem, doc.api_version)
        return pf.RawBlock(tex, format="latex")

    doc = doc.walk(action)
    return doc, findings


def apply_all(doc: pf.Doc) -> FilterResult:
    """Run all four filters in the fixed order documented above."""
    doc, heading_findings = normalize_headings(doc)
    doc = strip_word_junk(doc)
    doc, anchors = plant_anchors(doc)
    doc, table_findings = normalize_tables(doc)
    return FilterResult(doc=doc, anchors=anchors, findings=heading_findings + table_findings)
