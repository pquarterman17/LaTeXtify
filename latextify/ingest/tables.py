"""Word tables -> LaTeX ``tabular``/``longtable``, for well-formed tables.

Split out of :mod:`latextify.ingest.filters` (2026-08-10). pandoc's own LaTeX
table writer is bypassed entirely: it emits column specs that overflow a
two-column journal measure, and it has no notion of a table that should float.
This module renders each table itself -- column alignment inferred from the
CONTENT (numeric columns right-align), a booktabs rule structure, and a float
wrapper that promotes a wide table to the journal's two-column environment.

Tables this cannot faithfully represent (nested tables, pathological
merged-cell grids) are detected here by :func:`_pathology_reason` and handed
to :mod:`latextify.ingest.tables_degraded`, which reconstructs something
compilable and clearly marked rather than emitting silent corruption.
"""

from __future__ import annotations

import io
import re

import panflute as pf
import pypandoc

_NUMERIC_CELL_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?([eE][+-]?\d+)?%?$")

# pandoc's own Table colspec alignment, when it carries one, always wins over
# the numeric-majority inference below.
_PANDOC_ALIGN_TO_LATEX = {
    "AlignLeft": "l",
    "AlignRight": "r",
    "AlignCenter": "c",
}

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
    tex = pypandoc.convert_text(buf.getvalue(), to="latex", format="json", verify_format=False)
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
