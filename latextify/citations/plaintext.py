"""Plain-text citation reconstruction -- the mixed-collaborator safety net (item 14).

Used only when a manuscript carries no citation field codes at all (Zotero /
Mendeley / EndNote / Word-native): the caller (the emitter) checks
:func:`latextify.citations.fields.extract_field_citations` first and falls back
here. Three responsibilities:

1. **Segment** the typed reference list -- the numbered/indented paragraphs that
   follow a "References" / "Bibliography" heading in ``word/document.xml``
   (:func:`segment_reference_list`).
2. **Reconstruct** a bibliography by reconciling each reference against Crossref
   (:func:`reconstruct_citations` -> :mod:`latextify.citations.reconcile`),
   producing keyed ``RefEntry`` objects plus per-reference
   :class:`~latextify.model.reconcile.ReconcileRecord` confidence records.
3. **Link** the in-text markers left as literal body text into ``\\cite{...}``
   (:func:`link_body_markers`), and drop the now-duplicated typed reference list
   from the body (:func:`strip_reference_section`).

Marker forms are matched against pandoc's LaTeX output, NOT the raw Word text --
verified empirically (see the item 14 executor report):

    ``[12]``        -> ``{[}12{]}``            (pandoc brace-protects ``[`` / ``]``)
    ``[3-5,8]``     -> ``{[}3-5,8{]}``
    superscript N   -> ``\\textsuperscript{N,...}``
    ``(Smith et al., 2020)`` stays literal but pandoc may wrap it across a
        newline (``(Smith et al.,\\n2020)``); the author-year regex tolerates
        internal whitespace so the split marker still matches.

Numeric/superscript markers pair to reference-list positions (``[3-5]`` expands to
3, 4, 5). Author-year markers pair to reconstructed entries by first-author
surname + year. Any marker with no match is left untouched and reported as an
:class:`~latextify.model.emit.EmitWarning` message -- never a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..model.reconcile import ReconcileRecord, ReconciliationReport
from ..model.refs import RefEntry
from . import crossref, reconcile
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
# Leading list number on a reference paragraph: "12.", "12)", "[12]".
_LIST_NUMBER_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)\]])\s+")


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def _paragraph_text(paragraph) -> str:
    """Concatenate the visible ``w:t`` text of a paragraph."""
    return "".join(t.text or "" for t in paragraph.iter(_q("t")))


def _is_heading_paragraph(text: str) -> bool:
    return bool(_HEADING_RE.match(text)) and len(text.strip()) <= 40


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
    scope and would be collected too.
    """
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
    for paragraph in paragraphs[heading_index + 1 :]:
        text = _paragraph_text(paragraph).strip()
        if not text:
            continue
        match = _LIST_NUMBER_RE.match(text)
        if match:
            number = int(match.group(1) or match.group(2))
            body = text[match.end() :].strip()
            references.append(ReferenceItem(text=body, number=number))
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


def _build_author_year_index(entries: list[RefEntry]) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        if not entry.authors or not entry.year:
            continue
        first = entry.authors[0]
        surname = (first.family or first.literal).strip().lower()
        if not surname:
            continue
        index.setdefault((surname, entry.year), []).append(entry.key)
    return index


def reconstruct_citations(
    docx_path: Path | str,
    *,
    mailto: str | None = None,
    threshold: float = reconcile.DEFAULT_THRESHOLD,
    client: crossref.CrossrefClient | None = None,
) -> PlaintextResult:
    """Reconstruct a bibliography from a manuscript's typed reference list.

    If no reference list is found, returns an empty result with
    ``has_reference_list=False`` and makes NO network request. Otherwise queries
    Crossref (building a client from ``mailto`` when one is not injected) and
    reconciles each reference.
    """
    reflist = segment_reference_list(docx_path)
    if not reflist.found:
        return PlaintextResult(heading=reflist.heading, has_reference_list=False)

    owns_client = client is None
    if client is None:
        client = crossref.CrossrefClient(mailto=mailto)
    try:
        outcome = reconcile.reconcile_references(
            reflist.references, client, threshold=threshold
        )
    finally:
        if owns_client:
            client.close()

    keys_by_number = {
        record.ref_number: record.key
        for record in outcome.records
        if record.ref_number is not None
    }
    return PlaintextResult(
        entries=outcome.entries,
        records=outcome.records,
        keys_by_number=keys_by_number,
        author_year_keys=_build_author_year_index(outcome.entries),
        heading=reflist.heading,
        has_reference_list=True,
    )


# --- body marker detection + linkage -----------------------------------------

# pandoc brace-protects the square brackets of a numeric marker: [12] -> {[}12{]}.
NUMERIC_MARKER_RE = re.compile(r"\{\[\}([0-9][0-9\s,‒–—-]*)\{\]\}")
# A superscript run of numerals: \textsuperscript{2,4}.
SUPERSCRIPT_MARKER_RE = re.compile(r"\\textsuperscript\{([0-9][0-9\s,‒–—-]*)\}")
# (Smith et al., 2020) / (Smith and Jones, 2019) / (Smith, 2018); internal
# whitespace (incl. a pandoc line wrap) tolerated between author part and year.
AUTHOR_YEAR_MARKER_RE = re.compile(
    r"\("
    r"(?P<authors>[A-Z][A-Za-zÀ-ɏ.'`-]*"
    r"(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-zÀ-ɏ.'`-]*)?)"
    r"\s*,?\s+"
    r"(?P<year>(?:18|19|20)\d{2})[a-z]?"
    r"\)"
)
# A sectioning command wrapping a reference-list heading, in pandoc's LaTeX.
_REF_SECTION_RE = re.compile(r"\\(?:sub)*section\*?\{([^}]*)\}")
_RANGE_SEP = re.compile(r"[‒–—-]")


def expand_numeric_range(content: str) -> list[int]:
    """Expand a marker body like ``"3-5,8"`` into ``[3, 4, 5, 8]``.

    Accepts commas as separators, ASCII/Unicode hyphens/dashes as ranges. Returns
    numbers in written order (duplicates preserved by position, dropped later by
    the key de-dup). An unparseable chunk is skipped; an all-unparseable body
    yields ``[]`` (the marker is then treated as non-citation and left alone).
    """
    numbers: list[int] = []
    for chunk in content.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = _RANGE_SEP.split(chunk)
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            start, end = int(parts[0]), int(parts[1])
            if start <= end and end - start < 1000:  # guard against absurd ranges
                numbers.extend(range(start, end + 1))
            else:
                numbers.extend([start, end])
        elif chunk.isdigit():
            numbers.append(int(chunk))
    return numbers


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def strip_reference_section(tex: str, result: PlaintextResult) -> str:
    """Remove the typed reference list from the body (to EOF from its heading).

    The generated project renders the bibliography from ``references.bib`` via
    ``\\bibliography``; leaving the typed list in the body would duplicate it.
    Cuts from the first sectioning command whose title matches the reference-list
    heading to the end of the body. If no such heading is present in ``tex``
    (e.g. pandoc named it differently), the body is returned unchanged.
    """
    if not result.has_reference_list:
        return tex
    for match in _REF_SECTION_RE.finditer(tex):
        if _is_heading_paragraph(match.group(1).strip()):
            return tex[: match.start()].rstrip() + "\n"
    return tex


def link_body_markers(tex: str, result: PlaintextResult) -> tuple[str, list[str]]:
    """Replace resolvable in-text markers with ``\\cite{...}``.

    Returns the rewritten body and a de-duplicated list of warning messages for
    markers that looked like citations but could not be resolved. Numeric and
    superscript markers warn on an unresolved position; author-year markers warn
    only when the year is one the reconstructed bibliography actually contains
    (an unrecognized ``(Word, 1999)`` is far more likely to be ordinary prose).
    """
    messages: list[str] = []
    seen: set[str] = set()

    def warn(message: str) -> None:
        if message not in seen:
            seen.add(message)
            messages.append(message)

    known_years = {year for (_surname, year) in result.author_year_keys}

    def resolve_numeric(content: str, display: str) -> str | None:
        positions = expand_numeric_range(content)
        if not positions:
            return None  # not actually a numeric citation marker
        keys: list[str] = []
        missing: list[int] = []
        for position in positions:
            key = result.keys_by_number.get(position)
            (keys.append(key) if key else missing.append(position))
        if not keys:
            warn(f"citation marker '{display}' has no matching reconstructed reference")
            return None
        if missing:
            warn(
                f"citation marker '{display}' partly unresolved: "
                f"no reference numbered {', '.join(str(m) for m in missing)}"
            )
        return "\\cite{" + ",".join(_dedup(keys)) + "}"

    def numeric_sub(match: re.Match[str]) -> str:
        replacement = resolve_numeric(match.group(1), f"[{match.group(1).strip()}]")
        return replacement if replacement is not None else match.group(0)

    def superscript_sub(match: re.Match[str]) -> str:
        replacement = resolve_numeric(match.group(1), f"^{match.group(1).strip()}")
        return replacement if replacement is not None else match.group(0)

    def author_year_sub(match: re.Match[str]) -> str:
        authors = match.group("authors")
        year = match.group("year")
        surname = re.split(r"\s+", authors.strip())[0].strip(".,'`-").lower()
        keys = result.author_year_keys.get((surname, year))
        if keys:
            return "\\cite{" + ",".join(_dedup(list(keys))) + "}"
        if year in known_years:
            warn(
                f"author-year marker '({authors.strip()}, {year})' did not match "
                "any reconstructed reference"
            )
        return match.group(0)

    tex = NUMERIC_MARKER_RE.sub(numeric_sub, tex)
    tex = SUPERSCRIPT_MARKER_RE.sub(superscript_sub, tex)
    tex = AUTHOR_YEAR_MARKER_RE.sub(author_year_sub, tex)
    return tex, messages
