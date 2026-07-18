"""Parse a user-supplied PubMed MEDLINE ``.nbib`` export into :class:`RefEntry` records.

PubMed's "Send to > Citation manager" download is the MEDLINE tag format: a
2-4 character tag flush against the start of the line, then ``- ``, then the
value; a value that overflows one line continues on the next, indented with
spaces and carrying no tag of its own (:func:`_parse_fields` re-joins these
by looking at which lines start with whitespace). Records are normally
blank-line separated; a record boundary is also recognized wherever a new
``PMID`` tag starts, in case an export concatenates records with no blank
line between them.

Only the tags the pipeline needs are mapped: ``PMID`` (the record's own id
-> the entry's key, the same "use the source's own identifier" convention
:mod:`latextify.citations.bibtex_in` follows for a ``.bib`` entry's
citekey), ``TI`` (title), ``AU`` (one line per author, MEDLINE's abbreviated
"Surname Initials" form -- no comma), ``DP`` (date of publication -> year),
``JT``/``TA`` (journal, full title then abbreviated title), ``VI``/``IP``/
``PG`` (volume/issue/pages), and ``LID``/``AID`` -- a record can list
several identifiers under these tags; only the one suffixed ``[doi]`` is
used.
"""

from __future__ import annotations

import re

from ..model.refs import Name, RefEntry

_TAG_RE = re.compile(r"^([A-Za-z]{2,4})\s*-\s?(.*)$")
_YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
_DOI_SUFFIX_RE = re.compile(r"^(.*?)\s*\[doi\]\s*$", re.IGNORECASE)


def _split_records(text: str) -> list[list[str]]:
    """Split raw ``.nbib`` text into per-record line groups."""
    records: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = []
            continue
        match = _TAG_RE.match(line)
        if match and match.group(1).upper() == "PMID" and current:
            records.append(current)
            current = []
        current.append(line)
    if current:
        records.append(current)
    return records


def _parse_fields(lines: list[str]) -> dict[str, list[str]]:
    """Group a record's lines into ``{tag: [value, ...]}`` (repeated tags kept)."""
    fields: dict[str, list[str]] = {}
    tag: str | None = None
    for line in lines:
        match = _TAG_RE.match(line)
        if match:
            tag = match.group(1).upper()
            fields.setdefault(tag, []).append(match.group(2).strip())
        elif tag is not None and line[:1].isspace():
            fields[tag][-1] = f"{fields[tag][-1]} {line.strip()}".strip()
    return fields


def _first(fields: dict[str, list[str]], *tags: str) -> str | None:
    for tag in tags:
        for value in fields.get(tag, []):
            if value:
                return value
    return None


def _parse_author(raw: str) -> Name:
    """MEDLINE's abbreviated ``AU`` form is "Surname Initials" (no comma)."""
    words = raw.split()
    if len(words) < 2:
        return Name(literal=raw) if raw else Name()
    return Name(family=" ".join(words[:-1]), given=words[-1])


def _parse_doi(fields: dict[str, list[str]]) -> str | None:
    for tag in ("LID", "AID"):
        for value in fields.get(tag, []):
            match = _DOI_SUFFIX_RE.match(value)
            if match:
                return match.group(1).strip()
    return None


def _parse_year(fields: dict[str, list[str]]) -> str | None:
    dp = _first(fields, "DP")
    if not dp:
        return None
    match = _YEAR_RE.search(dp)
    return match.group(0) if match else None


def _to_refentry(fields: dict[str, list[str]]) -> RefEntry | None:
    pmid = _first(fields, "PMID")
    if not pmid:
        return None
    authors = tuple(_parse_author(a) for a in fields.get("AU", []) if a)
    return RefEntry(
        key=pmid,
        entry_type="article",
        title=_first(fields, "TI"),
        authors=authors,
        year=_parse_year(fields),
        container_title=_first(fields, "JT", "TA"),
        volume=_first(fields, "VI"),
        issue=_first(fields, "IP"),
        pages=_first(fields, "PG"),
        doi=_parse_doi(fields),
        source="nbib",
        raw_id=pmid,
    )


def parse_nbib(text: str) -> list[RefEntry]:
    """Parse PubMed MEDLINE ``text`` into a list of :class:`RefEntry` (order preserved).

    A record with no ``PMID`` tag is skipped -- no source identifier to use
    as a key -- rather than raised on, mirroring
    :func:`latextify.citations.bibtex_in.parse_bibtex` dropping a keyless
    ``.bib`` entry.

    Raises:
        ValueError: non-blank ``text`` contains no recognizable MEDLINE tag
            line at all (not a ``.nbib`` export).
    """
    if not text.strip():
        return []
    if not any(_TAG_RE.match(line) for line in text.splitlines()):
        raise ValueError("not a valid PubMed .nbib export (no MEDLINE tag lines found)")
    entries: list[RefEntry] = []
    for record_lines in _split_records(text):
        entry = _to_refentry(_parse_fields(record_lines))
        if entry is not None:
            entries.append(entry)
    return entries
