"""Parse a user-supplied ``.bib`` file into :class:`RefEntry` records.

This is the reader half of the bibliography story (``bib.py`` is the writer).
It exists so a manuscript's own reference-manager export can be matched against
its typed citations offline -- no Crossref round-trip -- see
:mod:`latextify.citations.bibmatch`.

The parser is deliberately small and forgiving: real ``.bib`` exports are
well-formed ``@type{key, field = {value} | "value" | bare, ...}`` blocks, so it
scans for those, brace/quote-balances each field value, and skips anything it
can't make sense of (``@string``/``@preamble``/``@comment`` macros, a malformed
entry) rather than raising. It is NOT a full BibTeX/@string-macro engine.
"""

from __future__ import annotations

import re

from ..model.refs import Name, RefEntry

_YEAR_RE = re.compile(r"(?:18|19|20)\d{2}")
# Entry header: @article{  or  @article(  -- capture the type.
_ENTRY_TYPE_RE = re.compile(r"@([A-Za-z]+)\s*[{(]")
_SKIP_TYPES = {"string", "preamble", "comment"}

# BibTeX field name -> RefEntry attribute (first-wins on synonyms).
_CONTAINER_FIELDS = ("journal", "journaltitle", "booktitle", "series")


def parse_bibtex(text: str) -> list[RefEntry]:
    """Parse BibTeX ``text`` into a list of :class:`RefEntry` (order preserved).

    Entries that cannot be parsed are skipped, not raised on -- a single bad
    entry never discards the rest of a user's library.
    """
    macros = _collect_string_macros(text)
    entries: list[RefEntry] = []
    for entry_type, inner in _iter_entries(text):
        if entry_type in _SKIP_TYPES:
            continue
        key, _, body = inner.partition(",")
        fields = _parse_fields(body, macros)
        entry = _to_refentry(entry_type, key.strip(), fields)
        if entry is not None:
            entries.append(entry)
    return entries


def _collect_string_macros(text: str) -> dict[str, str]:
    """Resolve ``@string{name = {value}}`` definitions to a ``{name: value}`` map.

    Reference-manager exports rarely use ``@string``, but JabRef / hand-kept
    libraries do (e.g. ``journal = np`` with ``@string{np = {Nature Physics}}``);
    resolving them keeps the emitted ``references.bib`` honest. Concatenation
    (``jan # " 2020"``) is out of scope.
    """
    macros: dict[str, str] = {}
    for entry_type, inner in _iter_entries(text):
        if entry_type != "string":
            continue
        name, sep, raw_value = inner.partition("=")
        if sep:
            macros[name.strip().lower()] = _clean_value(raw_value)
    return macros


def _iter_entries(text: str):
    """Yield ``(entry_type, inner)`` for each ``@type{...}`` block (inner unparsed)."""
    pos = 0
    for match in _ENTRY_TYPE_RE.finditer(text):
        if match.start() < pos:
            continue
        entry_type = match.group(1).lower()
        open_char = text[match.end() - 1]
        close_char = "}" if open_char == "{" else ")"
        end = _match_delimiter(text, match.end(), open_char, close_char)
        if end is None:
            continue
        pos = end + 1
        yield entry_type, text[match.end() : end]


def _match_delimiter(text: str, start: int, open_char: str, close_char: str) -> int | None:
    """Index of the delimiter that closes the entry opened just before ``start``.

    Tracks ``{}`` nesting (field values brace-balance); for a ``(``-opened entry
    the closing ``)`` is the first one seen at brace-depth 0.
    """
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            if open_char == "{" and depth == 0:
                return i
            depth -= 1
        elif char == close_char and open_char == "(" and depth == 0:
            return i
        i += 1
    return None


def _split_top_level(body: str) -> list[str]:
    """Split ``body`` on commas that sit outside braces and quotes."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    current: list[str] = []
    for char in body:
        if char == "{":
            depth += 1
            current.append(char)
        elif char == "}":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == '"' and depth == 0:
            in_quote = not in_quote
            current.append(char)
        elif char == "," and depth == 0 and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _parse_fields(body: str, macros: dict[str, str] | None = None) -> dict[str, str]:
    fields: dict[str, str] = {}
    for chunk in _split_top_level(body):
        name, sep, raw_value = chunk.partition("=")
        if not sep:
            continue
        key = name.strip().lower()
        value = _clean_value(raw_value, macros)
        if key and value and key not in fields:
            fields[key] = value
    return fields


def _clean_value(raw: str, macros: dict[str, str] | None = None) -> str:
    """Unwrap the ``{...}``/``"..."`` delimiter and flatten a field value to plain text.

    A bare (unbraced, unquoted) value is either a number (``volume = 12``) or a
    ``@string`` macro reference (``journal = np``); resolve the latter against
    ``macros`` when supplied.
    """
    value = raw.strip()
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        value = value[1:-1]
    elif len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    elif macros and value.lower() in macros:
        value = macros[value.lower()]
    # Drop case-protection braces and collapse the whitespace an export wraps at.
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", value).strip()


def _parse_authors(raw: str) -> tuple[Name, ...]:
    """Parse a BibTeX ``and``-separated author string into CSL-shaped names."""
    names: list[Name] = []
    for token in re.split(r"\s+and\s+", raw):
        token = token.strip()
        if not token:
            continue
        if token.lower() == "others":
            names.append(Name(literal="others"))
        elif "," in token:
            family, _, given = token.partition(",")
            names.append(Name(family=family.strip(), given=given.strip()))
        else:
            words = token.split()
            names.append(Name(family=words[-1], given=" ".join(words[:-1])))
    return tuple(names)


def _to_refentry(entry_type: str, key: str, fields: dict[str, str]) -> RefEntry | None:
    if not key:
        return None
    container = next((fields[name] for name in _CONTAINER_FIELDS if name in fields), None)
    year = fields.get("year")
    if not year:
        for source in (fields.get("date"), fields.get("year")):
            match = _YEAR_RE.search(source) if source else None
            if match:
                year = match.group(0)
                break
    elif (match := _YEAR_RE.search(year)) is not None:
        year = match.group(0)
    doi = fields.get("doi")
    if doi:
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE).strip()
    return RefEntry(
        key=key,
        entry_type=entry_type,
        title=fields.get("title"),
        authors=_parse_authors(fields["author"]) if fields.get("author") else (),
        year=year,
        container_title=container,
        publisher=fields.get("publisher"),
        volume=fields.get("volume"),
        issue=fields.get("number") or fields.get("issue"),
        pages=fields.get("pages"),
        doi=doi,
        url=fields.get("url"),
        isbn=fields.get("isbn"),
        source="bibfile",
        raw_id=key,
    )
