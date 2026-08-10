"""Title / author / affiliation heuristics for :mod:`latextify.ingest.metadata_guess`.

Split out of :mod:`latextify.ingest.metadata_guess` (2026-08-10), which sits
at its own line-count ratchet pin (``tests/test_repo_integrity.py``). This is
the densest and most failure-prone part of the metadata guess: matching
author names to affiliation paragraphs by superscript marker is genuinely
ambiguous in real manuscripts, so most of this module's length is the
fallback ladder in :func:`link_author_affiliations` and the marker tokenizer
in :func:`_split_marker_text` that a naive comma-split gets wrong (see its
docstring for the "1*" case).

Public entry points, called from
:func:`latextify.ingest.metadata_guess.guess_meta` and
:func:`latextify.ingest.metadata_guess.front_matter_span` (the latter
re-runs the SAME detection to find where the docx title page ends, so both
callers must see identical behavior here):

    guess_title                  -- Title-styled paragraph, else largest-font
    guess_authors                -- names + raw superscript markers from the
                                     author line
    guess_affiliations           -- affiliation paragraph(s) following the
                                     authors, each with its own optional
                                     leading marker
    link_author_affiliations     -- resolves each author's raw markers to
                                     0-based indices into the affiliation
                                     list, preferring paragraph-label matches
                                     over first-seen-order guessing

Nothing here writes a ``# CHECK:`` comment directly -- every function returns
a ``checks: list[str]`` that the caller collects; ``guess_meta`` assembles
them into the ``MetaGuess.checks`` mapping that
:func:`latextify.ingest.metadata_schema.render_paper_yaml` turns into
comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from latextify.ingest.metadata_markers import (
    ABSTRACT_HEADING_RE,
    CORRESPONDING_RE,
    EMAIL_RE,
    KEYWORDS_RE,
)
from latextify.ingest.metadata_paragraphs import Para
from latextify.model.meta import Author

# A single superscript run can pack an affiliation reference and a
# corresponding-author symbol together with NO separator, e.g. "1*", "*1",
# "2†". Splitting only on commas/whitespace (the old behavior) left "1*" as one
# token that is neither ``isalnum()`` (so never read as affiliation 1) nor
# cleanly a symbol -- the digit was silently lost, and the author ended up with
# no affiliation (which then mis-attaches to the next affiliation block under
# REVTeX). Instead tokenize each run into maximal ALPHANUMERIC groups (each an
# affiliation marker: "1", "12", "a", or a sub-affiliation label "1a") and
# individual NON-alphanumeric symbols (each a corresponding-author flag: "*",
# "†", "‡", "§"). Comma/semicolon/whitespace remain pure separators -- never
# flags -- so "1,2" still yields ["1", "2"], not ["1", ",", "2"].
_MARKER_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[^0-9A-Za-z\s,;]")
_AUTHOR_SEP_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
_LEADING_MARKER_RE = re.compile(r"[0-9a-zA-Z]{1,3}")


def _split_marker_text(marker_text: str) -> list[str]:
    """Tokenize a superscript marker run into affiliation markers + flags.

    See :data:`_MARKER_TOKEN_RE` for the tokenization rule. Downstream,
    :func:`guess_authors` classifies each token: ``isalnum()`` tokens are
    affiliation markers, the rest are corresponding-author flags. So a bare
    ``"1*"`` correctly yields affiliation ``"1"`` AND a corresponding flag.
    """
    return _MARKER_TOKEN_RE.findall(marker_text)


def guess_title(paras: list[Para]) -> tuple[str, int, list[str]]:
    """Returns (title, paragraph_index_used, checks). index is -1 if none found."""
    for i, p in enumerate(paras):
        text = p.text.strip()
        if text and p.style_id and p.style_id.lower() == "title":
            return text, i, []

    candidates = [(i, p) for i, p in enumerate(paras[:5]) if p.text.strip()]
    if not candidates:
        return "", -1, ["no non-empty paragraphs found in the scanned range; title left empty."]

    best_i, best_p = max(candidates, key=lambda ip: (ip[1].font_size or 0, -ip[0]))
    checks = [
        "no paragraph uses the Title style; guessed from the largest-font "
        "paragraph among the first few instead — verify."
    ]
    return best_p.text.strip(), best_i, checks


@dataclass
class AuthorGuessResult:
    authors: list[Author]
    next_idx: int
    checks: list[str]
    expected_affiliation_count: int = 0
    # Per-author alnum (affiliation-only, non-corresponding) markers, aligned
    # index-for-index with ``authors``. Resolution into Author.affiliations
    # indices is deferred to link_author_affiliations, which runs once the
    # affiliation paragraphs (and any markers THEY carry) are known.
    raw_markers: list[tuple[str, ...]] = field(default_factory=list)
    # Distinct alnum markers in the order they were first seen scanning the
    # author line left to right -- kept for the rule-3 fallback only.
    marker_first_seen_order: list[str] = field(default_factory=list)


def guess_authors(paras: list[Para], start_idx: int) -> AuthorGuessResult:
    idx = start_idx
    while idx < len(paras) and not paras[idx].text.strip():
        idx += 1
    if idx >= len(paras):
        return AuthorGuessResult([], idx, ["no author line found after the title."])

    author_para = paras[idx]
    raw_authors: list[tuple[str, list[str]]] = []
    name_parts: list[str] = []
    markers: list[str] = []

    def flush() -> None:
        name = "".join(name_parts).strip(" ,;")
        if name:
            raw_authors.append((name, list(markers)))
        name_parts.clear()
        markers.clear()

    for seg in author_para.segments:
        if seg.superscript:
            markers.extend(_split_marker_text(seg.text))
            continue
        parts = _AUTHOR_SEP_RE.split(seg.text)
        if len(parts) == 1:
            name_parts.append(seg.text)
            continue
        name_parts.append(parts[0])
        flush()
        for mid in parts[1:-1]:
            name_parts.append(mid)
            flush()
        name_parts.append(parts[-1])
    flush()

    next_idx = idx + 1
    if not raw_authors:
        checks = ["could not parse any author names from the line following the title."]
        return AuthorGuessResult([], next_idx, checks)

    has_markers = any(m for _, m in raw_authors)
    if not has_markers:
        authors = [Author(name=name) for name, _ in raw_authors]
        checks = [
            "no superscript affiliation markers found on the author line; "
            "affiliation assignment could not be inferred — verify manually."
        ]
        return AuthorGuessResult(authors, next_idx, checks, expected_affiliation_count=0)

    marker_first_seen_order: list[str] = []
    for _, marker_list in raw_authors:
        for m in marker_list:
            if m.isalnum() and m not in marker_first_seen_order:
                marker_first_seen_order.append(m)

    authors: list[Author] = []
    raw_markers: list[tuple[str, ...]] = []
    corresponding_names: list[str] = []
    for name, marker_list in raw_authors:
        alnum_markers = tuple(m for m in marker_list if m.isalnum())
        is_corresponding = any(not m.isalnum() for m in marker_list)
        if is_corresponding:
            corresponding_names.append(name)
        authors.append(Author(name=name, corresponding=is_corresponding))
        raw_markers.append(alnum_markers)

    checks: list[str] = []
    if len(corresponding_names) > 1:
        checks.append(
            f"multiple authors flagged as corresponding ({', '.join(corresponding_names)}); verify."
        )

    return AuthorGuessResult(
        authors,
        next_idx,
        checks,
        expected_affiliation_count=len(marker_first_seen_order),
        raw_markers=raw_markers,
        marker_first_seen_order=marker_first_seen_order,
    )


def _split_leading_marker(p: Para) -> tuple[str | None, str]:
    """Split a paragraph's own leading superscript marker from its text.

    Returns ``(marker, remaining_text)`` when the paragraph opens with a
    superscript run matching a short alnum marker (e.g. an affiliation
    paragraph prefixed by "1" or "a"); returns ``(None, full_text)``
    otherwise. Capturing the marker (instead of discarding it) is what lets
    affiliation paragraphs be cross-validated against author markers by
    VALUE, rather than relying on physical/first-seen order alone.
    """
    segs = p.segments
    if segs and segs[0].superscript and _LEADING_MARKER_RE.fullmatch(segs[0].text.strip()):
        marker = segs[0].text.strip()
        return marker, "".join(s.text for s in segs[1:]).strip()
    return None, p.text.strip()


@dataclass
class AffiliationEntry:
    """One consumed affiliation paragraph: its own leading marker (if any) and text."""

    marker: str | None
    text: str


def guess_affiliations(
    paras: list[Para], start_idx: int, expected_count: int
) -> tuple[list[AffiliationEntry], int, list[str]]:
    entries: list[AffiliationEntry] = []
    idx = start_idx
    while idx < len(paras):
        text = paras[idx].text.strip()
        if not text:
            idx += 1
            continue
        if ABSTRACT_HEADING_RE.match(text) or KEYWORDS_RE.match(text):
            break
        if EMAIL_RE.search(text) and CORRESPONDING_RE.search(text):
            idx += 1
            continue
        marker, stripped = _split_leading_marker(paras[idx])
        entries.append(AffiliationEntry(marker=marker, text=stripped))
        idx += 1
        if expected_count and len(entries) >= expected_count:
            break

    checks: list[str] = []
    if expected_count and len(entries) != expected_count:
        checks.append(
            f"expected {expected_count} affiliation(s) based on author markers but found "
            f"{len(entries)}; verify the affiliation list and ordering."
        )
    elif not expected_count and not entries:
        checks.append("no affiliation lines found; affiliations left empty.")
    elif not expected_count and entries:
        checks.append(
            "affiliations were guessed positionally (no author markers to anchor them); verify."
        )

    return entries, idx, checks


def link_author_affiliations(
    raw_markers: list[tuple[str, ...]],
    marker_first_seen_order: list[str],
    affiliation_entries: list[AffiliationEntry],
) -> tuple[list[tuple[int, ...]], list[str]]:
    """Resolve each author's raw markers to 0-based indices into ``affiliation_entries``.

    Cross-validates marker VALUES rather than trusting physical/first-seen
    order alone, in order of preference:

      1. Affiliation paragraphs carry their own leading markers -- match
         each author marker to the paragraph whose OWN marker equals it,
         wherever it physically sits. An author marker with no matching
         label is dropped (CHECK, naming the marker); a labeled paragraph no
         author references is kept in the affiliation list but flagged
         (CHECK).
      2. No affiliation paragraph carries a marker, but every referenced
         author marker is numeric -- marker N means "the Nth affiliation
         paragraph" (1-based, by VALUE, not first-seen order). An
         out-of-range N is dropped (CHECK).
      3. Otherwise (non-numeric markers, unlabeled paragraphs) -- fall back
         to first-seen-order positional mapping (the pre-fix behavior), but
         flag it (CHECK) whenever that order is not already ascending,
         since first-seen order is then just a guess.

    Returns ``(per_author_affiliation_indices, checks)``, index-aligned with
    ``raw_markers``.
    """
    checks: list[str] = []
    n_affiliations = len(affiliation_entries)
    labeled_indices = {
        entry.marker: i for i, entry in enumerate(affiliation_entries) if entry.marker is not None
    }

    if labeled_indices:
        marker_to_index: dict[str, int] = {}
        for m in marker_first_seen_order:
            if m in labeled_indices:
                marker_to_index[m] = labeled_indices[m]
            else:
                checks.append(
                    f"author marker '{m}' has no matching affiliation paragraph label; "
                    "the reference was dropped -- verify."
                )
        referenced = set(marker_to_index.values())
        for i, entry in enumerate(affiliation_entries):
            if entry.marker is not None and i not in referenced:
                checks.append(
                    f"affiliation paragraph labeled '{entry.marker}' is not referenced by "
                    "any author marker; verify."
                )
    elif marker_first_seen_order and all(m.isdigit() for m in marker_first_seen_order):
        marker_to_index = {}
        for m in marker_first_seen_order:
            n = int(m)
            if 1 <= n <= n_affiliations:
                marker_to_index[m] = n - 1
            else:
                checks.append(
                    f"author marker '{m}' has no matching affiliation paragraph (only "
                    f"{n_affiliations} found); the reference was dropped -- verify."
                )
    else:
        marker_to_index = {m: n for n, m in enumerate(marker_first_seen_order)}
        if marker_first_seen_order != sorted(marker_first_seen_order):
            checks.append("affiliation assignment inferred from marker appearance order; verify.")

    per_author = [
        tuple(marker_to_index[m] for m in markers if m in marker_to_index)
        for markers in raw_markers
    ]
    return per_author, checks
