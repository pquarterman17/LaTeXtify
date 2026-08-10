"""Plain-text citation reconstruction -- the mixed-collaborator safety net (item 14).

Used only when a manuscript carries no citation field codes at all (Zotero /
Mendeley / EndNote / Word-native): the caller (the emitter) checks
:func:`latextify.citations.fields.extract_field_citations` first and falls back
here. Three responsibilities, split across this module and its companion
:mod:`latextify.citations.body_markers`:

1. **Segment** the typed reference list -- the numbered/indented paragraphs that
   follow a "References" / "Bibliography" heading in ``word/document.xml``
   (:func:`segment_reference_list`).
2. **Reconstruct** a bibliography by reconciling each reference against Crossref
   (:func:`reconstruct_citations` -> :mod:`latextify.citations.reconcile`),
   producing keyed ``RefEntry`` objects plus per-reference
   :class:`~latextify.model.reconcile.ReconcileRecord` confidence records.
3. **Link** the in-text markers left as literal body text into ``\\cite{...}``,
   and drop the now-duplicated typed reference list from the body -- both live
   in :mod:`latextify.citations.body_markers`
   (:func:`~latextify.citations.body_markers.link_body_markers` /
   :func:`~latextify.citations.body_markers.strip_reference_section`), split out
   on 2026-08-10 to stay clear of this file's own line-count ratchet pin
   (``tests/test_repo_integrity.py``).

This module owns steps 1 and 2, plus the :class:`PlaintextResult` data contract
both modules build and consume; :mod:`.body_markers` owns step 3 and documents
the marker-format details (how pandoc renders ``[12]``, superscripts, and
author-year parentheticals) in its own docstring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..ingest.formats import is_docx
from ..model.reconcile import ReconcileRecord, ReconciliationReport
from ..model.refs import RefEntry
from . import crossref, reconcile
from .authoryear_index import (  # noqa: F401 -- _raw_leading_surname re-exported for tests
    _raw_leading_surname,
    build_author_year_index,
)
from .fields import read_document_xml
from .reconcile import ReferenceItem

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# --- reference-list heading recognition --------------------------------------

_HEADING_KEYWORDS = (
    "references and notes",
    "reference list",
    "references",
    "reference",
    "bibliography",
    "works cited",
    "literature cited",
)
# A paragraph is a reference-list heading when its trimmed text is exactly one of
# the keywords (optionally a trailing colon / leading numbering like "5.").
_HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?\s+)?(" + "|".join(re.escape(k) for k in _HEADING_KEYWORDS) + r")\s*:?\s*$",
    re.IGNORECASE,
)
# Leading list number on a reference paragraph: "12.", "12)", "[12]", "(12)".
# The bracket/paren forms are self-delimiting (their own closing punctuation
# unambiguously ends the marker), so the trailing separator is OPTIONAL --
# real manuscripts often type "[4]B. L. Giles, ..." with no space after the
# bracket. Without tolerating that, the whole "[4]B. L. Giles, ..." string
# never matches at all: ref_number stays None AND the raw "[4]" leaks into
# the text handed to Crossref/raw-entry emission (poisoning both the query
# and the generated BibTeX key, e.g. an observed key "4b2015"). The bare
# "N." / "N)" / "N]" form keeps a MANDATORY trailing space -- "3.14" at a
# paragraph's start must never be misread as reference number 3.
_LIST_NUMBER_RE = re.compile(
    r"^\s*(?:"
    r"\[(?P<br>\d+)\]\s*"
    r"|\((?P<pr>\d+)\)\s*"
    r"|(?P<dot>\d+)[.)\]]\s+"
    r")"
)


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def _paragraph_text(paragraph) -> str:
    """Concatenate the visible ``w:t`` text of a paragraph."""
    return "".join(t.text or "" for t in paragraph.iter(_q("t")))


def _is_heading_paragraph(text: str) -> bool:
    return bool(_HEADING_RE.match(text)) and len(text.strip()) <= 40


def is_reference_heading_text(text: str) -> bool:
    """True when ``text`` (a whole paragraph/heading's text, already stripped)
    reads as a reference-list heading -- "References", "Bibliography", "Works
    Cited", ... (see :data:`_HEADING_KEYWORDS`), not just a sentence that
    happens to mention one.

    Public wrapper around :func:`_is_heading_paragraph` -- the same
    classification :func:`segment_reference_list` uses to find a manuscript's
    typed reference-list heading in its raw OOXML paragraphs. Reused by
    :mod:`latextify.emit.alt_formats` to find (and strip) the SAME heading in
    a panflute AST ``Header`` block, so the HTML/Markdown export path doesn't
    need its own copy of :data:`_HEADING_KEYWORDS`/:data:`_HEADING_RE`. Also
    reused by :mod:`latextify.citations.body_markers` (which sits across the
    module boundary from the private ``_is_heading_paragraph`` it would
    otherwise need) to find the SAME heading in already-rendered LaTeX text.
    """
    return _is_heading_paragraph(text)


def _has_list_numbering(paragraph) -> bool:
    """True when a paragraph carries real Word list numbering (``w:numPr``).

    Word's "Numbering" toolbar button records list membership this way; the
    displayed "1.", "2.", ... is rendered by Word from the list definition and
    never appears as literal text in any ``w:t`` run, unlike a typed "1. Smith
    ..." reference (which :data:`_LIST_NUMBER_RE` already handles). A
    ``w:numId`` of ``"0"`` is Word's own convention for "numbering removed
    from this paragraph" and does not count.
    """
    p_pr = paragraph.find(_q("pPr"))
    if p_pr is None:
        return False
    num_pr = p_pr.find(_q("numPr"))
    if num_pr is None:
        return False
    num_id = num_pr.find(_q("numId"))
    return num_id is None or num_id.get(_q("val")) != "0"


@dataclass
class ReferenceList:
    """The typed reference list segmented from a document."""

    heading: str | None
    references: list[ReferenceItem] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.heading is not None and bool(self.references)


def segment_reference_list(docx_path: Path | str) -> ReferenceList:
    """Find the "References"/"Bibliography" heading and collect what follows.

    Returns every subsequent non-empty paragraph as a :class:`ReferenceItem`
    (leading list numbers parsed into ``number``), to the end of the body.
    References are conventionally the last section; content after them is out of
    scope and would be collected too. A non-.docx manuscript (no
    ``word/document.xml`` to read) dispatches to
    :mod:`latextify.citations.reflist_nondocx` instead.
    """
    if not is_docx(docx_path):
        from .reflist_nondocx import segment_reference_list_from_manuscript

        return segment_reference_list_from_manuscript(Path(docx_path))
    root = etree.fromstring(read_document_xml(docx_path))
    paragraphs = list(root.iter(_q("p")))

    heading_index: int | None = None
    heading_text: str | None = None
    for index, paragraph in enumerate(paragraphs):
        text = _paragraph_text(paragraph)
        if _is_heading_paragraph(text):
            heading_index = index
            heading_text = text.strip().rstrip(":").strip()
            break

    if heading_index is None:
        return ReferenceList(heading=None)

    references: list[ReferenceItem] = []
    auto_number = 0
    for paragraph in paragraphs[heading_index + 1 :]:
        text = _paragraph_text(paragraph).strip()
        if not text:
            continue
        match = _LIST_NUMBER_RE.match(text)
        if match:
            number = int(match.group("br") or match.group("pr") or match.group("dot"))
            body = text[match.end() :].strip()
            references.append(ReferenceItem(text=body, number=number))
        elif _has_list_numbering(paragraph):
            # Word's own auto-numbering: no typed digits to parse, so assign
            # sequential numbers in document order (a fresh Word list always
            # starts at 1 and increments by 1, matching what the reader sees).
            auto_number += 1
            references.append(ReferenceItem(text=text, number=auto_number))
        else:
            references.append(ReferenceItem(text=text, number=None))

    return ReferenceList(heading=heading_text, references=references)


# --- reconstruction ----------------------------------------------------------


@dataclass
class PlaintextResult:
    """Everything the emitter needs to wire a plain-text-cited manuscript.

    ``entries`` feed ``references.bib``; ``report`` (and ``records``) feed the
    conversion report; ``keys_by_number`` resolves numeric/superscript markers;
    ``author_year_keys`` resolves ``(Surname, YEAR)`` markers. ``has_reference_list``
    is ``False`` when no typed bibliography was found -- the emitter then leaves
    the body untouched (nothing to reconstruct or link).
    """

    entries: list[RefEntry] = field(default_factory=list)
    records: tuple[ReconcileRecord, ...] = field(default_factory=tuple)
    keys_by_number: dict[int, str] = field(default_factory=dict)
    author_year_keys: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    heading: str | None = None
    has_reference_list: bool = False

    @property
    def report(self) -> ReconciliationReport:
        return ReconciliationReport(records=self.records)


def reconstruct_citations(
    docx_path: Path | str,
    *,
    mailto: str | None = None,
    threshold: float = reconcile.DEFAULT_THRESHOLD,
    client: crossref.CrossrefClient | None = None,
    bib_entries: list[RefEntry] | None = None,
) -> PlaintextResult:
    """Reconstruct a bibliography from a manuscript's typed reference list.

    If no reference list is found, returns an empty result with
    ``has_reference_list=False`` and makes NO network request. Otherwise
    reconciles each reference: against ``bib_entries`` (the author's own ``.bib``
    export) first when supplied, then Crossref for anything the ``.bib`` doesn't
    cover (building a client from ``mailto`` when one is not injected). A
    reference list fully covered by ``bib_entries`` therefore never touches the
    network.
    """
    reflist = segment_reference_list(docx_path)
    if not reflist.found:
        return PlaintextResult(heading=reflist.heading, has_reference_list=False)

    owns_client = client is None
    if client is None:
        client = crossref.CrossrefClient(mailto=mailto)
    try:
        outcome = reconcile.reconcile_references(
            reflist.references, client, threshold=threshold, bib_entries=bib_entries
        )
    finally:
        if owns_client:
            client.close()

    keys_by_number = {
        record.ref_number: record.key for record in outcome.records if record.ref_number is not None
    }
    return PlaintextResult(
        entries=outcome.entries,
        records=outcome.records,
        keys_by_number=keys_by_number,
        author_year_keys=build_author_year_index(outcome.entries),
        heading=reflist.heading,
        has_reference_list=True,
    )
