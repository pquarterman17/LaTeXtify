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
        record.ref_number: record.key
        for record in outcome.records
        if record.ref_number is not None
    }
    return PlaintextResult(
        entries=outcome.entries,
        records=outcome.records,
        keys_by_number=keys_by_number,
        author_year_keys=build_author_year_index(outcome.entries),
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
# An EndNote TEMPORARY (unformatted) citation the author never "updated":
# "{Davies, 2004 #78}" -- and consecutive/semicolon-joined runs of them. pandoc
# escapes the braces and hash, so in the LaTeX body it reads
# "\{Davies, 2004 \#78\}"; the ``#<record>`` is the tell that distinguishes it
# from ordinary braced prose. Matched as a RUN so "{A, 2004 #1}{B, 2005 #2}"
# (or a tripled paste of the same cite) collapses to one \cite{...}.
_ENDNOTE_TEMP_RUN_RE = re.compile(r"(?:\\\{[^{}]*?\\#[0-9]+[^{}]*?\\\})+")
# One "Surname, Year" author-date pair inside such a marker.
_ENDNOTE_SEG_RE = re.compile(
    r"(?P<surname>[A-Z][A-Za-zÀ-ɏ.'`-]+)\s*,\s*(?P<year>(?:18|19|20)\d{2})"
)
# A sectioning command wrapping a reference-list heading, in pandoc's LaTeX.
_REF_SECTION_RE = re.compile(r"\\(?:sub)*section\*?\{([^}]*)\}")
# A run of one-or-more dash characters separates a numeric range's endpoints.
# The ``+`` is load-bearing: pandoc's LaTeX writer renders a typed en dash as
# literal ASCII "--" (and an em dash as "---"), so a single-marker range like
# "[8-10]" reaches expand_numeric_range as the body "8--10". With a single-dash
# separator that splits to ["8", "", "10"] (three parts) and the range check
# ``len(parts) == 2`` fails, silently dropping the whole marker -- the observed
# "[8-10]"/"[11-13]"/"[19-23]" left as literal text in a real manuscript. (The
# separate _BRACKET_JOIN_RE handles the DIFFERENT case of two bracket markers
# joined by a dash, "{[}1{]}--{[}3{]}"; this handles the dash INSIDE one marker.)
_RANGE_SEP = re.compile(r"[‒–—-]+")

# pandoc brace-protects/escapes EACH "[12]" or "^12^" marker individually, so a
# typed range like "[1]-[3]" (or its superscript equivalent) never reaches
# NUMERIC_MARKER_RE / SUPERSCRIPT_MARKER_RE as one group with "1-3" inside --
# it reaches them as TWO separate groups joined by a bare dash: "{[}1{]}--{[}3{]}"
# / "\textsuperscript{1}--\textsuperscript{3}" (verified against real pandoc 3.9
# output). Left alone, only the first and last marker would resolve and the
# range's middle would be silently dropped. Merge adjacent same-kind groups
# joined by nothing but a dash (and optional whitespace) into one group BEFORE
# matching, so the existing range-expansion logic in expand_numeric_range
# handles the merged content exactly like a typed "[1-3]".
#
# pandoc's LaTeX writer renders a typed en dash as literal ASCII "--" (and an
# em dash as "---"), not the unicode dash character itself, so the separator
# between two joined groups must accept a RUN of one or more dash characters,
# not just a single one.
_BRACKET_JOIN_RE = re.compile(
    r"\{\[\}([0-9][0-9\s,‒–—-]*)\{\]\}\s*[‒–—-]+\s*\{\[\}([0-9][0-9\s,‒–—-]*)\{\]\}"
)
_SUPERSCRIPT_JOIN_RE = re.compile(
    r"\\textsuperscript\{([0-9][0-9\s,‒–—-]*)\}\s*[‒–—-]+\s*"
    r"\\textsuperscript\{([0-9][0-9\s,‒–—-]*)\}"
)


def _merge_dash_joined_markers(tex: str, pattern: re.Pattern[str], wrap) -> str:
    """Repeatedly fold ``pattern``-matched adjacent marker pairs into one.

    A chain of more than two dash-joined markers (e.g. ``[1]-[3]-[5]``) needs
    more than one pass since a single :func:`re.sub` call does not re-scan its
    own replacements; looping to a fixed point handles that rare case too.
    """
    while True:
        new_tex, count = pattern.subn(
            lambda m: wrap(f"{m.group(1)}-{m.group(2)}"), tex
        )
        if count == 0:
            return new_tex
        tex = new_tex


# The seven canonical low-index cubic crystallographic directions/planes
# (Miller notation) NOT already caught by the leading-zero check below:
# 110, 101, 011, 111 (001, 010, 100 all have a leading zero and are already
# excluded that way). Real manuscripts commonly write these bare -- "grown
# along the [001], [110], and [111] directions" is a stock materials-science
# phrase. Deliberately narrow (an exact closed set, not a general "all digits
# are 0/1" rule) to minimize the chance of ever suppressing a genuine
# reference number in an unusually long reference list.
_MILLER_INDEX_TRIADS = frozenset({"110", "101", "011", "111"})


def _is_plain_number(chunk: str) -> bool:
    """True for a chunk that reads as an ordinary reference number.

    All digits, no leading zero, and not one of the non-zero-padded
    :data:`_MILLER_INDEX_TRIADS`. A leading zero -- ``"001"``, ``"010"``,
    ``"100"`` -- is virtually always a crystallographic direction/plane index
    (Miller notation), never a citation: a typed reference list is never
    padded with leading zeros. Excluding these here (rather than in
    ``NUMERIC_MARKER_RE``, which cannot know whether a bracketed number is a
    citation without also knowing the reconstructed reference count) keeps a
    genuine list like ``"[1,2]"`` unaffected while ``"[001]"``/``"[110]"``/
    ``"[111]"`` are left untouched as ordinary body text.
    """
    chunk = chunk.strip()
    if not chunk.isdigit():
        return False
    if len(chunk) > 1 and chunk[0] == "0":
        return False
    return chunk not in _MILLER_INDEX_TRIADS


def expand_numeric_range(content: str) -> list[int]:
    """Expand a marker body like ``"3-5,8"`` into ``[3, 4, 5, 8]``.

    Accepts commas as separators, ASCII/Unicode hyphens/dashes as ranges. Returns
    numbers in written order (duplicates preserved by position, dropped later by
    the key de-dup). An unparseable chunk is skipped -- this includes any chunk
    (or range endpoint) with a leading zero, see :func:`_is_plain_number` -- and
    an all-unparseable body yields ``[]`` (the marker is then treated as
    non-citation and left alone).
    """
    numbers: list[int] = []
    for chunk in content.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = _RANGE_SEP.split(chunk)
        if len(parts) == 2 and _is_plain_number(parts[0]) and _is_plain_number(parts[1]):
            start, end = int(parts[0]), int(parts[1])
            if start <= end and end - start < 1000:  # guard against absurd ranges
                numbers.extend(range(start, end + 1))
            else:
                numbers.extend([start, end])
        elif _is_plain_number(chunk):
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


# A reference heading pandoc rendered WITHOUT a \section wrapper -- either a
# bare "References" paragraph or a bold \textbf{References} line. Section-heading
# promotion (latextify.ingest.filters.promote_pseudo_headings) turns an ALL-CAPS
# "REFERENCES" into a real \section that the primary path below catches, but a
# Title-case/bold "References" is not ALL-CAPS and slips through -- this is the
# fallback that still strips the typed list in that case.
_TEXTBF_LINE_RE = re.compile(r"^\s*\\textbf\{(.*)\}\s*$")


def _find_bare_reference_heading(tex: str) -> int | None:
    """Offset of the first body line that reads as a reference-list heading.

    Scans line by line, unwrapping a lone ``\\textbf{...}`` bold wrapper, and
    tests each candidate with :func:`_is_heading_paragraph` -- which requires
    the WHOLE line to be a reference keyword (``References``, ``Bibliography``,
    ...), so an in-sentence "the references show ..." never matches. Returns
    ``None`` when no such heading line is present.
    """
    offset = 0
    for line in tex.splitlines(keepends=True):
        stripped = line.strip()
        bold = _TEXTBF_LINE_RE.match(stripped)
        candidate = bold.group(1).strip() if bold else stripped
        if candidate and _is_heading_paragraph(candidate):
            return offset
        offset += len(line)
    return None


def strip_reference_section_to_eof(tex: str) -> str:
    """Cut from a reference-list heading (to EOF) out of ``tex``.

    Prefers a real ``\\section{References}`` (headings are promoted upstream);
    falls back to a bold/bare "References" line that was never promoted
    (Title-case, not ALL-CAPS). Returns ``tex`` unchanged if no reference-list
    heading is present. Shared by both citation paths: the plaintext path
    (below) strips the typed list it reconstructed, and the field-code path
    (:mod:`latextify.emit.project`) strips the reference manager's own
    formatted bibliography, which duplicates the generated ``\\bibliography``.
    """
    for match in _REF_SECTION_RE.finditer(tex):
        if _is_heading_paragraph(match.group(1).strip()):
            return tex[: match.start()].rstrip() + "\n"
    offset = _find_bare_reference_heading(tex)
    if offset is not None:
        return tex[:offset].rstrip() + "\n"
    return tex


def strip_reference_section(tex: str, result: PlaintextResult) -> str:
    """Remove the typed reference list from the body (to EOF from its heading).

    The generated project renders the bibliography from ``references.bib`` via
    ``\\bibliography``; leaving the typed list in the body would duplicate it
    (and, because each retained entry gets a ``\\cite`` prepended, render it a
    second time with scrambled numbering). Unchanged if this manuscript had no
    reconstructed reference list, or if no reference heading is present.
    """
    if not result.has_reference_list:
        return tex
    return strip_reference_section_to_eof(tex)


def _body_start_index(tex: str) -> int:
    """Index in ``tex`` where the manuscript body begins.

    Defined as the first sectioning command (``\\section``, ``\\subsection``,
    ...) -- the same boundary REVTeX/IEEEtran/elsarticle preambles draw
    between the title/author/affiliation block and the body. A manuscript's
    Abstract, when typed as a Level-1 Word heading (the common case), lands
    right at this same boundary, so no separate "abstract" case is needed.

    Used to suppress false-positive superscript CITATION detection on
    title-page AFFILIATION superscripts ("J. Smith\\textsuperscript{1}, A.
    Doe\\textsuperscript{2}") -- a manuscript's first real citation never
    lands before its own first heading. Falls back to ``0`` (nothing
    excluded) when no sectioning command is found at all, e.g. a bare
    fragment under test with no headings of its own.
    """
    match = _REF_SECTION_RE.search(tex)
    return match.start() if match else 0


def link_body_markers(tex: str, result: PlaintextResult) -> tuple[str, list[str]]:
    """Replace resolvable in-text markers with ``\\cite{...}``.

    Returns the rewritten body and a de-duplicated list of warning messages for
    markers that looked like citations but could not be resolved. Numeric and
    superscript markers warn on an unresolved position; author-year markers warn
    only when the year is one the reconstructed bibliography actually contains
    (an unrecognized ``(Word, 1999)`` is far more likely to be ordinary prose).
    Superscript markers before :func:`_body_start_index`'s boundary are title-page
    affiliation markers, never citations -- skipped entirely, no warning. Markers
    at or after that boundary are handled exactly as before, including a genuine
    out-of-range mismatch (a real body citation whose number(s) exceed the
    reconstructed reference count) still warning -- only the title-page position
    is special-cased, never the resolution logic itself.
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

    def superscript_sub(match: re.Match[str], *, body_start: int) -> str:
        if match.start() < body_start:
            # Title-page affiliation superscript (see _body_start_index) --
            # never a citation marker; leave untouched, no warning.
            return match.group(0)
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

    def endnote_temp_sub(match: re.Match[str]) -> str:
        run = match.group(0)
        keys: list[str] = []
        unresolved: list[str] = []
        for seg in _ENDNOTE_SEG_RE.finditer(run):
            surname = seg.group("surname").strip(".,'`-").lower()
            year = seg.group("year")
            found = result.author_year_keys.get((surname, year))
            if found:
                keys.extend(found)
            else:
                unresolved.append(f"{seg.group('surname')}, {year}")
        if not keys:
            # Never fabricate a citation: leave the marker literal but flag it so
            # the author can fix an EndNote field that was never updated/matched.
            if unresolved:
                warn(
                    "EndNote temporary citation "
                    f"'{{{'; '.join(unresolved)}}}' did not match any reconstructed "
                    "reference; left as typed -- update the field or fix the reference."
                )
            return run
        if unresolved:
            warn(
                "EndNote temporary citation partly unresolved: "
                f"no reference for {'; '.join(unresolved)}"
            )
        return "\\cite{" + ",".join(_dedup(keys)) + "}"

    tex = _merge_dash_joined_markers(
        tex, _BRACKET_JOIN_RE, lambda content: "{[}" + content + "{]}"
    )
    tex = _merge_dash_joined_markers(
        tex, _SUPERSCRIPT_JOIN_RE, lambda content: "\\textsuperscript{" + content + "}"
    )
    tex = NUMERIC_MARKER_RE.sub(numeric_sub, tex)
    body_start = _body_start_index(tex)
    tex = SUPERSCRIPT_MARKER_RE.sub(
        lambda match: superscript_sub(match, body_start=body_start), tex
    )
    tex = AUTHOR_YEAR_MARKER_RE.sub(author_year_sub, tex)
    tex = _ENDNOTE_TEMP_RUN_RE.sub(endnote_temp_sub, tex)
    return tex, messages
