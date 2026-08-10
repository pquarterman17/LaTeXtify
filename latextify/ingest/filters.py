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

import re
from dataclasses import dataclass, field

import panflute as pf

from latextify.ingest.headings import _title_inlines, normalize_headings, promote_pseudo_headings
from latextify.ingest.tables import _is_nested_table, _pathology_reason, _table_to_latex
from latextify.ingest.tables_degraded import _degraded_table_to_latex
from latextify.model import FilterFinding


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
