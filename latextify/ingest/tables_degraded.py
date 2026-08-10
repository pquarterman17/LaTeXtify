"""Reconstruction for tables that cannot be represented faithfully (item 25).

Split out of :mod:`latextify.ingest.filters` (2026-08-10). Some Word tables --
nested tables, merged-cell grids whose rows disagree about column count --
have no honest ``tabular`` equivalent. The choice is between emitting
something that silently corrupts the data and emitting something simplified
that SAYS it is simplified. This module does the second: it expands the grid
into explicit slots, flattens nested content to text, and marks the result
with ``[table structure simplified -- verify against source]`` so a reader
knows to check it against the manuscript.

:func:`_pathology_reason` in :mod:`latextify.ingest.tables` decides which
tables come here.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import panflute as pf
import pypandoc

from latextify.ingest.tables import (
    _NUMERIC_CELL_RE,
    _PANDOC_ALIGN_TO_LATEX,
    _table_body_rows,
    _wrap_table_float,
)

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
    tex = pypandoc.convert_text(buf.getvalue(), to="latex", format="json", verify_format=False)
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


def _degraded_row_to_latex(slots: list[_GridSlot], api_version, cache: dict[int, str]) -> str:
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
