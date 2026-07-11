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

Every function here mutates the ``panflute.Doc`` in place (via ``Doc.walk``)
and also returns it, so callers can chain: ``doc = normalize_headings(doc)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import panflute as pf

from latextify.model import FilterFinding

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


def apply_all(doc: pf.Doc) -> FilterResult:
    """Run all three filters in the fixed order documented above."""
    doc, heading_findings = normalize_headings(doc)
    doc = strip_word_junk(doc)
    doc, anchors = plant_anchors(doc)
    return FilterResult(doc=doc, anchors=anchors, findings=heading_findings)
