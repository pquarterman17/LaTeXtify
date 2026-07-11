"""RefEntry -> BibTeX emission with stable, de-collided citation keys.

Responsibilities (plan item 7, third sub-task):

* Map CSL item types to BibTeX entry types (``csl_type_to_bibtex``).
* Generate stable citation keys ``<firstauthor-lastname><year><first-title-word>``,
  ASCII-folded and lower-cased, with a/b/c suffixes for collisions
  (``make_base_key`` / ``assign_keys``).
* Render each ``RefEntry`` to a BibTeX record, escaping LaTeX-special
  characters in values, brace-protecting internal capitals in titles, and
  placing the DOI in the ``doi`` field (``to_bibtex`` / ``entries_to_bib``).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import replace

from ..model.refs import Name, RefEntry

# --- CSL type -> BibTeX type -------------------------------------------------

_CSL_TO_BIBTEX = {
    "article-journal": "article",
    "article": "article",
    "article-magazine": "article",
    "article-newspaper": "article",
    "paper-conference": "inproceedings",
    "book": "book",
    "chapter": "incollection",
    "entry-encyclopedia": "incollection",
    "report": "techreport",
    "thesis": "phdthesis",
    "manuscript": "unpublished",
    "webpage": "misc",
    "dataset": "misc",
    "patent": "misc",
}


def csl_type_to_bibtex(csl_type: str | None) -> str:
    """Return the BibTeX entry type for a CSL type, defaulting to ``misc``."""
    return _CSL_TO_BIBTEX.get((csl_type or "").strip(), "misc")


# --- ASCII folding + key generation ------------------------------------------

# Characters NFKD does not decompose to ASCII; map them explicitly.
_FOLD_MAP = {
    "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th",
    "ł": "l", "Ł": "L", "ı": "i", "ħ": "h", "ĸ": "k", "ŉ": "n",
}

_TITLE_STOPWORDS = {
    "the", "a", "an", "on", "of", "in", "and", "for", "to", "with", "from",
    "at", "by", "as", "into", "over", "via", "using", "toward", "towards",
}


def ascii_fold(text: str) -> str:
    """Transliterate accented/latin-extended characters to plain ASCII."""
    mapped = "".join(_FOLD_MAP.get(ch, ch) for ch in text)
    nfkd = unicodedata.normalize("NFKD", mapped)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", ascii_fold(text)).lower()


def _author_token(entry: RefEntry) -> str:
    if not entry.authors:
        return ""
    first = entry.authors[0]
    if first.is_literal:
        # Institutional name: use the first word for a compact, stable key.
        words = [w for w in re.split(r"\s+", first.literal) if w]
        return _slug(words[0]) if words else ""
    return _slug(first.family)


def _first_title_word(title: str | None) -> str:
    if not title:
        return ""
    words = [_slug(w) for w in re.split(r"\s+", title)]
    words = [w for w in words if w]
    if not words:
        return ""
    for w in words:
        if w not in _TITLE_STOPWORDS:
            return w
    return words[0]  # everything was a stopword; fall back to the first


def make_base_key(entry: RefEntry) -> str:
    """Build the (pre-collision) citation key for a reference.

    Format: ``<firstauthor-lastname><year><first-title-word>``. Missing year
    becomes ``nd``; missing author falls back to the first title word + year,
    or ``anon`` + year when there is no title either.
    """
    author = _author_token(entry)
    year = entry.year or "nd"
    title_word = _first_title_word(entry.title)
    if author:
        return f"{author}{year}{title_word}"
    if title_word:
        return f"{title_word}{year}"
    return f"anon{year}"


def _suffix(n: int) -> str:
    # a, b, ... z, then za, zb, ... (only matters past 26 collisions).
    if n < 26:
        return chr(ord("a") + n)
    return "z" + _suffix(n - 26)


def assign_keys(entries: list[RefEntry]) -> list[RefEntry]:
    """Return copies of ``entries`` with unique ``key`` values assigned.

    Entries sharing a base key are disambiguated with a/b/c... suffixes in
    order of first appearance. Order of the input list is preserved.
    """
    bases = [make_base_key(e) for e in entries]
    counts = Counter(bases)
    used: dict[str, int] = defaultdict(int)
    out: list[RefEntry] = []
    for entry, base in zip(entries, bases, strict=True):
        if counts[base] > 1:
            key = base + _suffix(used[base])
            used[base] += 1
        else:
            key = base
        out.append(replace(entry, key=key))
    return out


# --- LaTeX escaping + BibTeX rendering ---------------------------------------

_LATEX_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_DOI_MAP = {"_": r"\_", "&": r"\&", "%": r"\%", "#": r"\#", "{": r"\{", "}": r"\}"}


def escape_latex(text: str) -> str:
    """Escape LaTeX-special characters in a field value."""
    return "".join(_LATEX_MAP.get(ch, ch) for ch in text)


def _escape_doi(doi: str) -> str:
    # DOIs are consumed by hyperref/doi as a URL fragment: only neutralize the
    # characters that would break LaTeX, leave slashes/dots/tildes intact.
    return "".join(_DOI_MAP.get(ch, ch) for ch in doi)


def _needs_brace_protection(word: str) -> bool:
    # Protect a word whose capitalization must survive title-casing bib styles:
    # any uppercase letter after the first alphabetic character (GaAs, CO2, H2O,
    # pH). A leading capital alone (ordinary title-cased word) is not protected.
    seen_first_alpha = False
    for ch in word:
        if ch.isalpha():
            if seen_first_alpha and ch.isupper():
                return True
            seen_first_alpha = True
    return False


def protect_title(title: str) -> str:
    """Escape a title and brace-protect words with internal capitals."""
    parts = re.split(r"(\s+)", title)
    out: list[str] = []
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        escaped = escape_latex(part)
        out.append("{" + escaped + "}" if _needs_brace_protection(part) else escaped)
    return "".join(out)


def _format_name(name: Name) -> str:
    if name.is_literal:
        return "{" + escape_latex(name.literal) + "}"
    if name.given:
        return f"{escape_latex(name.family)}, {escape_latex(name.given)}"
    return escape_latex(name.family or name.literal)


def _format_authors(authors: tuple[Name, ...]) -> str:
    return " and ".join(_format_name(a) for a in authors)


def _format_pages(pages: str) -> str:
    m = re.match(r"^\s*([A-Za-z0-9]+)\s*[-‒–—]\s*([A-Za-z0-9]+)\s*$", pages)
    if m:
        return f"{m.group(1)}--{m.group(2)}"
    return escape_latex(pages)


_CONTAINER_FIELD = {
    "article": "journal",
    "inproceedings": "booktitle",
    "incollection": "booktitle",
    "inbook": "booktitle",
}


def to_bibtex(entry: RefEntry) -> str:
    """Render a single ``RefEntry`` to a BibTeX record."""
    fields: list[tuple[str, str]] = []
    if entry.authors:
        fields.append(("author", _format_authors(entry.authors)))
    if entry.title:
        fields.append(("title", protect_title(entry.title)))
    if entry.container_title:
        container_key = _CONTAINER_FIELD.get(entry.entry_type)
        if container_key:
            fields.append((container_key, escape_latex(entry.container_title)))
    if entry.publisher:
        fields.append(("publisher", escape_latex(entry.publisher)))
    if entry.year:
        fields.append(("year", escape_latex(entry.year)))
    if entry.volume:
        fields.append(("volume", escape_latex(entry.volume)))
    if entry.issue:
        fields.append(("number", escape_latex(entry.issue)))
    if entry.pages:
        fields.append(("pages", _format_pages(entry.pages)))
    if entry.doi:
        fields.append(("doi", _escape_doi(entry.doi)))
    if entry.url:
        fields.append(("url", entry.url))
    if entry.isbn:
        fields.append(("isbn", escape_latex(entry.isbn)))

    body = ",\n".join(f"  {key} = {{{value}}}" for key, value in fields)
    return f"@{entry.entry_type}{{{entry.key},\n{body}\n}}\n"


def entries_to_bib(entries: list[RefEntry]) -> str:
    """Render a list of references to a full ``.bib`` file body."""
    return "\n".join(to_bibtex(e) for e in entries)
