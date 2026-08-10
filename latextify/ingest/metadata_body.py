"""Abstract / keywords / corresponding-email heuristics, and the section-heading
detector used to bound them.

Split out of :mod:`latextify.ingest.metadata_guess` (2026-08-10), which sits
at its own line-count ratchet pin (``tests/test_repo_integrity.py``). Grouped
separately from :mod:`latextify.ingest.metadata_authors` because these
heuristics scan FORWARD from a known anchor (the "Abstract" heading, a
"Keywords:" line) rather than parsing a single fixed-position paragraph, and
they all lean on :func:`_looks_like_section_heading` to know where to stop --
a manuscript with no Word heading style still usually types "INTRODUCTION"
or "I. Introduction" in the body, and the abstract must not swallow it.

Public entry points, called from
:func:`latextify.ingest.metadata_guess.guess_meta` and
:func:`latextify.ingest.metadata_guess.front_matter_span`:

    guess_abstract            -- text between the "Abstract" heading and the
                                  next heading/Keywords line
    guess_keywords            -- terms on a "Keywords:" line
    find_corresponding_email  -- an email near a "correspond" mention or a
                                  bare "*" marker, bounded to the title page
    title_page_end_index      -- how far find_corresponding_email is allowed
                                  to search (never into the abstract body,
                                  which can mention an unrelated email)
    abstract_heading_index,
    keywords_line_index       -- index-only siblings of guess_abstract /
                                  guess_keywords, used by
                                  ``front_matter_span`` to find the SAME span
                                  ``guess_meta`` consumed, without re-parsing
                                  the text back out of it
"""

from __future__ import annotations

import re

from latextify.ingest.metadata_markers import (
    ABSTRACT_HEADING_RE,
    CORRESPONDING_RE,
    EMAIL_RE,
    KEYWORDS_RE,
)
from latextify.ingest.metadata_paragraphs import Para

# A roman-numeral section heading typed inline, e.g. "I. Introduction".
_ROMAN_SECTION_RE = re.compile(r"^[IVXLC]+\.\s+\S")


def _looks_like_section_heading(para: Para) -> bool:
    """Heuristic: does this paragraph read as a body SECTION heading?

    Terminates abstract consumption (and thus the front-matter span) at the
    start of the body. Real manuscripts frequently type section headings as
    bare all-caps or roman-numbered lines with NO Word heading style
    (e.g. "INTRODUCTION", "I. Introduction"), so a style-only check misses
    them and the abstract swallows the whole body. Over-detection here is
    safe (the abstract ends early -> the extra text stays in the body, i.e.
    merely duplicated, never lost); under-detection is the failure mode to
    avoid (the abstract would consume, and the emitter would then strip, the
    real body).
    """
    if para.style_id and "heading" in para.style_id.lower():
        return True
    text = para.text.strip()
    if not text or len(text) > 60:
        return False
    if text[-1] in ".!?:;,":
        return False  # trailing sentence/label punctuation -> not a bare heading
    if _ROMAN_SECTION_RE.match(text):
        return True  # "I. Introduction", "II. Methods"
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)  # "INTRODUCTION"


def guess_abstract(paras: list[Para], start_idx: int) -> tuple[str, int, list[str]]:
    heading_idx = None
    for i in range(start_idx, len(paras)):
        if ABSTRACT_HEADING_RE.match(paras[i].text.strip()):
            heading_idx = i
            break

    if heading_idx is None:
        checks = ["no 'Abstract' heading found in the scanned range; abstract left empty."]
        return "", start_idx, checks

    parts: list[str] = []
    idx = heading_idx + 1
    while idx < len(paras):
        text = paras[idx].text.strip()
        if not text:
            idx += 1
            continue
        if KEYWORDS_RE.match(text) or _looks_like_section_heading(paras[idx]):
            break
        parts.append(text)
        idx += 1

    abstract = " ".join(parts).strip()
    checks = (
        []
        if abstract
        else ["found an 'Abstract' heading but no following text; abstract left empty."]
    )
    return abstract, idx, checks


def guess_keywords(paras: list[Para], start_idx: int) -> tuple[list[str], list[str]]:
    for i in range(start_idx, len(paras)):
        text = paras[i].text.strip()
        m = KEYWORDS_RE.match(text)
        if m:
            kws = [k.strip() for k in re.split(r"[;,]", m.group(1)) if k.strip()]
            if not kws:
                return [], ["found a 'Keywords:' line but could not parse any terms from it."]
            return kws, []
    return [], ["no 'Keywords:' line found in the scanned range; keywords left empty."]


def find_corresponding_email(paras: list[Para]) -> str | None:
    for p in paras:
        text = p.text.strip()
        if not text:
            continue
        match = EMAIL_RE.search(text)
        if match and (CORRESPONDING_RE.search(text) or text.startswith("*")):
            return match.group(0)
    return None


def title_page_end_index(paras: list[Para]) -> int:
    """Index of the first 'Abstract' heading paragraph, or ``len(paras)`` if none.

    Bounds how far :func:`find_corresponding_email` is allowed to search: the
    corresponding-author contact line always lives in the title-page block
    (title/authors/affiliations), never inside the abstract body -- scanning
    past the heading risks matching an unrelated email mentioned in the
    abstract text itself (e.g. a data-availability statement), especially
    since abstracts often contain the word "correspondence" in an unrelated
    sense (e.g. "in correspondence with prior work").
    """
    for i, p in enumerate(paras):
        if ABSTRACT_HEADING_RE.match(p.text.strip()):
            return i
    return len(paras)


def abstract_heading_index(paras: list[Para], start_idx: int) -> int | None:
    """Index of the first 'Abstract' heading at/after ``start_idx``, or None.

    Mirrors the heading scan inside :func:`guess_abstract`; kept separate so
    :func:`latextify.ingest.metadata_guess.front_matter_span` can tell
    "abstract heading present" apart from "abstract body empty" (both leave
    :func:`guess_abstract`'s returned text "").
    """
    for i in range(start_idx, len(paras)):
        if ABSTRACT_HEADING_RE.match(paras[i].text.strip()):
            return i
    return None


def keywords_line_index(paras: list[Para], start_idx: int) -> int | None:
    """Index of the first 'Keywords:' line at/after ``start_idx``, or None.

    Mirrors the scan inside :func:`guess_keywords`.
    """
    for i in range(start_idx, len(paras)):
        if KEYWORDS_RE.match(paras[i].text.strip()):
            return i
    return None
