"""paper.yaml metadata sidecar: heuristic extraction, schema, write-once I/O.

Plan item 8. Scans the first ~20 paragraphs of ``word/document.xml`` (parsed
directly with lxml, the same approach as ``preflight.py``) and guesses:

    title         -- Title-styled paragraph, else the largest-font paragraph
                     among the first few (flagged low confidence)
    authors       -- the paragraph following the title; superscript runs are
                     read as affiliation markers (digits/letters) or, if
                     non-alphanumeric (``*``, ``†``), a corresponding-author flag
    affiliations  -- the paragraph(s) following the author line, one per
                     distinct affiliation marker referenced by an author.
                     Authors are linked to affiliation text by marker VALUE,
                     not by physical/first-seen position: an affiliation
                     paragraph's own leading marker (when present) is
                     matched against author markers regardless of order;
                     failing that, all-numeric markers are matched
                     positionally by value; only as a last resort (no
                     labels, non-numeric markers) does first-seen order
                     apply, and that fallback is itself flagged unless the
                     order is already unambiguous.
    abstract      -- paragraph(s) following a paragraph whose text is exactly
                     "Abstract"
    keywords      -- a "Keywords:" line, split on commas/semicolons

Every heuristic is conservative: whenever a guess is not well supported by
the document (no Title style found, no affiliation markers, no Abstract
heading, ...) the guess is still made on a best-effort basis but the field is
recorded in the returned ``MetaGuess.checks`` mapping, which
``render_paper_yaml`` turns into ``# CHECK:`` comments in the emitted file.
Nothing is ever silently confident.

``paper.yaml`` is written only if absent (write-once). Once it exists it is
the source of truth: ``load_meta`` parses and validates it, raising
``MetaValidationError`` naming the offending field on any schema violation.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from latextify.model.meta import Affiliation, Author, Meta

# IR convention (model/meta.py): Author.affiliations are 0-based indices into
# Meta.affiliations. The paper.yaml FILE stays 1-based (matching the visible
# superscript markers in the manuscript); conversion happens only here, at the
# render/load boundary.

DEFAULT_SIDECAR_NAME = "paper.yaml"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NSMAP = {"w": _W_NS}

_ABSTRACT_HEADING_RE = re.compile(r"^abstract$", re.IGNORECASE)
_KEYWORDS_RE = re.compile(r"^(?:keywords|key\s*words)\s*[:.]\s*(.*)$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[\w-]+")
_CORRESPONDING_RE = re.compile(r"correspond", re.IGNORECASE)
_MARKER_SPLIT_RE = re.compile(r"[,\s]+")
_AUTHOR_SEP_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
_LEADING_MARKER_RE = re.compile(r"[0-9a-zA-Z]{1,3}")

_TOP_LEVEL_FIELD_ORDER = ("title", "authors", "affiliations", "abstract", "keywords")


# --------------------------------------------------------------------------
# docx paragraph extraction
# --------------------------------------------------------------------------


@dataclass
class _Segment:
    text: str
    superscript: bool


@dataclass
class _Para:
    style_id: str | None
    segments: list[_Segment] = field(default_factory=list)
    font_size: int | None = None  # max half-point run size seen in this paragraph

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


def _qn(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _read_document_root(docx_path: Path):
    from lxml import etree

    try:
        archive = zipfile.ZipFile(docx_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{docx_path}: not a valid .docx ({exc})") from exc
    with archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError(f"{docx_path}: not a valid .docx (missing word/document.xml)")
        with archive.open("word/document.xml") as fh:
            try:
                return etree.parse(fh).getroot()
            except etree.XMLSyntaxError as exc:
                raise ValueError(
                    f"{docx_path}: not a valid .docx "
                    f"(malformed XML in word/document.xml: {exc})"
                ) from exc


def _extract_paragraphs(root, limit: int) -> list[_Para]:
    body = root.find("w:body", _NSMAP)
    if body is None:
        return []

    paras: list[_Para] = []
    for p in body.findall("w:p", _NSMAP):
        if len(paras) >= limit:
            break

        style_el = p.find("w:pPr/w:pStyle", _NSMAP)
        style_id = style_el.get(_qn("val")) if style_el is not None else None

        segments: list[_Segment] = []
        max_size: int | None = None
        for run in p.findall("w:r", _NSMAP):
            text = "".join(t.text or "" for t in run.findall("w:t", _NSMAP))
            vert = run.find("w:rPr/w:vertAlign", _NSMAP)
            is_super = vert is not None and vert.get(_qn("val")) == "superscript"
            sz_el = run.find("w:rPr/w:sz", _NSMAP)
            if sz_el is not None:
                try:
                    sz = int(sz_el.get(_qn("val")))
                except (TypeError, ValueError):
                    sz = None
                if sz is not None and (max_size is None or sz > max_size):
                    max_size = sz
            if text:
                segments.append(_Segment(text=text, superscript=is_super))

        paras.append(_Para(style_id=style_id, segments=segments, font_size=max_size))

    return paras


# --------------------------------------------------------------------------
# heuristics
# --------------------------------------------------------------------------


def _split_marker_text(marker_text: str) -> list[str]:
    return [m for m in _MARKER_SPLIT_RE.split(marker_text.strip()) if m]


def _guess_title(paras: list[_Para]) -> tuple[str, int, list[str]]:
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
class _AuthorGuessResult:
    authors: list[Author]
    next_idx: int
    checks: list[str]
    expected_affiliation_count: int = 0
    # Per-author alnum (affiliation-only, non-corresponding) markers, aligned
    # index-for-index with ``authors``. Resolution into Author.affiliations
    # indices is deferred to _link_author_affiliations, which runs once the
    # affiliation paragraphs (and any markers THEY carry) are known.
    raw_markers: list[tuple[str, ...]] = field(default_factory=list)
    # Distinct alnum markers in the order they were first seen scanning the
    # author line left to right -- kept for the rule-3 fallback only.
    marker_first_seen_order: list[str] = field(default_factory=list)


def _guess_authors(paras: list[_Para], start_idx: int) -> _AuthorGuessResult:
    idx = start_idx
    while idx < len(paras) and not paras[idx].text.strip():
        idx += 1
    if idx >= len(paras):
        return _AuthorGuessResult([], idx, ["no author line found after the title."])

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
        return _AuthorGuessResult([], next_idx, checks)

    has_markers = any(m for _, m in raw_authors)
    if not has_markers:
        authors = [Author(name=name) for name, _ in raw_authors]
        checks = [
            "no superscript affiliation markers found on the author line; "
            "affiliation assignment could not be inferred — verify manually."
        ]
        return _AuthorGuessResult(authors, next_idx, checks, expected_affiliation_count=0)

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

    return _AuthorGuessResult(
        authors,
        next_idx,
        checks,
        expected_affiliation_count=len(marker_first_seen_order),
        raw_markers=raw_markers,
        marker_first_seen_order=marker_first_seen_order,
    )


def _split_leading_marker(p: _Para) -> tuple[str | None, str]:
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
class _AffiliationEntry:
    """One consumed affiliation paragraph: its own leading marker (if any) and text."""

    marker: str | None
    text: str


def _guess_affiliations(
    paras: list[_Para], start_idx: int, expected_count: int
) -> tuple[list[_AffiliationEntry], int, list[str]]:
    entries: list[_AffiliationEntry] = []
    idx = start_idx
    while idx < len(paras):
        text = paras[idx].text.strip()
        if not text:
            idx += 1
            continue
        if _ABSTRACT_HEADING_RE.match(text) or _KEYWORDS_RE.match(text):
            break
        if _EMAIL_RE.search(text) and _CORRESPONDING_RE.search(text):
            idx += 1
            continue
        marker, stripped = _split_leading_marker(paras[idx])
        entries.append(_AffiliationEntry(marker=marker, text=stripped))
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


def _link_author_affiliations(
    raw_markers: list[tuple[str, ...]],
    marker_first_seen_order: list[str],
    affiliation_entries: list[_AffiliationEntry],
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


def _guess_abstract(paras: list[_Para], start_idx: int) -> tuple[str, int, list[str]]:
    heading_idx = None
    for i in range(start_idx, len(paras)):
        if _ABSTRACT_HEADING_RE.match(paras[i].text.strip()):
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
        style_is_heading = bool(paras[idx].style_id and "heading" in paras[idx].style_id.lower())
        if _KEYWORDS_RE.match(text) or style_is_heading:
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


def _guess_keywords(paras: list[_Para], start_idx: int) -> tuple[list[str], list[str]]:
    for i in range(start_idx, len(paras)):
        text = paras[i].text.strip()
        m = _KEYWORDS_RE.match(text)
        if m:
            kws = [k.strip() for k in re.split(r"[;,]", m.group(1)) if k.strip()]
            if not kws:
                return [], ["found a 'Keywords:' line but could not parse any terms from it."]
            return kws, []
    return [], ["no 'Keywords:' line found in the scanned range; keywords left empty."]


def _find_corresponding_email(paras: list[_Para]) -> str | None:
    for p in paras:
        text = p.text.strip()
        if not text:
            continue
        match = _EMAIL_RE.search(text)
        if match and (_CORRESPONDING_RE.search(text) or text.startswith("*")):
            return match.group(0)
    return None


def _title_page_end_index(paras: list[_Para]) -> int:
    """Index of the first 'Abstract' heading paragraph, or ``len(paras)`` if none.

    Bounds how far :func:`_find_corresponding_email` is allowed to search: the
    corresponding-author contact line always lives in the title-page block
    (title/authors/affiliations), never inside the abstract body -- scanning
    past the heading risks matching an unrelated email mentioned in the
    abstract text itself (e.g. a data-availability statement), especially
    since abstracts often contain the word "correspondence" in an unrelated
    sense (e.g. "in correspondence with prior work").
    """
    for i, p in enumerate(paras):
        if _ABSTRACT_HEADING_RE.match(p.text.strip()):
            return i
    return len(paras)


# --------------------------------------------------------------------------
# public guess entry point
# --------------------------------------------------------------------------


@dataclass
class MetaGuess:
    """Result of guessing metadata from a manuscript: the IR plus low-confidence notes."""

    meta: Meta
    checks: dict[str, list[str]]


def guess_meta(docx_path: Path | str, *, max_paragraphs: int = 20) -> MetaGuess:
    """Guess title-page metadata from the first ``max_paragraphs`` paragraphs."""
    root = _read_document_root(Path(docx_path))
    paras = _extract_paragraphs(root, max_paragraphs)

    title, title_idx, title_checks = _guess_title(paras)
    author_result = _guess_authors(paras, max(title_idx + 1, 0))
    affiliation_entries, aff_end_idx, aff_checks = _guess_affiliations(
        paras, author_result.next_idx, author_result.expected_affiliation_count
    )
    affiliations = [e.text for e in affiliation_entries]
    abstract, abstract_end_idx, abstract_checks = _guess_abstract(paras, aff_end_idx)
    keywords, keyword_checks = _guess_keywords(paras, abstract_end_idx)

    authors = list(author_result.authors)
    author_checks = list(author_result.checks)

    if author_result.expected_affiliation_count:
        per_author_affs, link_checks = _link_author_affiliations(
            author_result.raw_markers, author_result.marker_first_seen_order, affiliation_entries
        )
        aff_checks.extend(link_checks)
        authors = [
            replace(author, affiliations=affs)
            for author, affs in zip(authors, per_author_affs, strict=True)
        ]

    # Affiliation indices on each Author come from markers seen on the author
    # line, but _guess_affiliations may come up short of matching paragraphs
    # (or find none at all) -- a document ending abruptly, an affiliation
    # marker with no corresponding paragraph, etc. An out-of-range index left
    # in place here would build a Meta that meta_from_yaml_data itself would
    # reject as invalid once this guess is round-tripped through paper.yaml,
    # crashing the *next* run (load_or_create_meta only guesses once and
    # trusts the sidecar thereafter) instead of surfacing here. Drop any
    # reference past the end of the guessed affiliation list and flag it.
    dropped_reference = False
    for i, author in enumerate(authors):
        valid = tuple(idx for idx in author.affiliations if idx < len(affiliations))
        if len(valid) != len(author.affiliations):
            dropped_reference = True
        authors[i] = replace(author, affiliations=valid)
    if dropped_reference:
        aff_checks.append(
            "an author referenced an affiliation marker with no matching "
            "affiliation paragraph; the reference was dropped -- verify the "
            "affiliation list and author markers."
        )

    corresponding_idxs = [i for i, a in enumerate(authors) if a.corresponding]
    if len(corresponding_idxs) == 1:
        # Never search past the abstract heading -- the abstract body is not
        # part of the title page and can easily contain an unrelated email
        # (data availability, a mentioned prior study, ...) alongside the
        # word "correspondence" in a sense that has nothing to do with the
        # corresponding author.
        email = _find_corresponding_email(paras[: _title_page_end_index(paras)])
        if email:
            authors[corresponding_idxs[0]] = replace(authors[corresponding_idxs[0]], email=email)
        else:
            author_checks.append(
                "a corresponding author was marked but no nearby email address was found; verify."
            )

    meta = Meta(
        title=title,
        authors=tuple(authors),
        affiliations=tuple(Affiliation(name=a) for a in affiliations),
        abstract=abstract,
        keywords=tuple(keywords),
    )
    checks = {
        "title": title_checks,
        "authors": author_checks,
        "affiliations": aff_checks,
        "abstract": abstract_checks,
        "keywords": keyword_checks,
    }
    checks = {k: v for k, v in checks.items() if v}
    return MetaGuess(meta=meta, checks=checks)


# --------------------------------------------------------------------------
# schema validation
# --------------------------------------------------------------------------


class MetaValidationError(ValueError):
    """Raised when a paper.yaml sidecar fails schema validation.

    The message always names the offending field (e.g. ``authors[0].name``)
    so the error is actionable without having to open the file.
    """


def _field_error(source: str, field_name: str, msg: str) -> MetaValidationError:
    return MetaValidationError(f"{source}: field '{field_name}' {msg}")


def meta_from_yaml_data(data: object, *, source: str = DEFAULT_SIDECAR_NAME) -> Meta:
    """Validate a parsed-YAML mapping against the paper.yaml schema and build a Meta."""
    if not isinstance(data, dict):
        raise MetaValidationError(f"{source}: root must be a mapping, got {type(data).__name__}")

    if "title" not in data:
        raise MetaValidationError(f"{source}: missing required field 'title'")
    title = data["title"]
    if not isinstance(title, str) or not title.strip():
        raise _field_error(source, "title", "must be a non-empty string")

    if "affiliations" not in data:
        raise MetaValidationError(f"{source}: missing required field 'affiliations'")
    raw_affiliations = data["affiliations"]
    if not isinstance(raw_affiliations, list):
        raise _field_error(source, "affiliations", "must be a list of strings")
    affiliations: list[str] = []
    for i, item in enumerate(raw_affiliations):
        if not isinstance(item, str) or not item.strip():
            raise MetaValidationError(
                f"{source}: field 'affiliations[{i}]' must be a non-empty string"
            )
        affiliations.append(item)

    if "authors" not in data:
        raise MetaValidationError(f"{source}: missing required field 'authors'")
    raw_authors = data["authors"]
    if not isinstance(raw_authors, list) or not raw_authors:
        raise _field_error(source, "authors", "must be a non-empty list")

    authors: list[Author] = []
    for i, raw in enumerate(raw_authors):
        prefix = f"authors[{i}]"
        if not isinstance(raw, dict):
            raise MetaValidationError(f"{source}: field '{prefix}' must be a mapping")

        if "name" not in raw:
            raise MetaValidationError(f"{source}: missing required field '{prefix}.name'")
        name = raw["name"]
        if not isinstance(name, str) or not name.strip():
            raise MetaValidationError(f"{source}: field '{prefix}.name' must be a non-empty string")

        raw_affs = raw.get("affiliations", [])
        if not isinstance(raw_affs, list):
            raise MetaValidationError(
                f"{source}: field '{prefix}.affiliations' must be a list of integers"
            )
        affs: list[int] = []
        for j, aff_idx in enumerate(raw_affs):
            if not isinstance(aff_idx, int) or isinstance(aff_idx, bool):
                raise MetaValidationError(
                    f"{source}: field '{prefix}.affiliations[{j}]' must be an integer"
                )
            if not (1 <= aff_idx <= len(affiliations)):
                raise MetaValidationError(
                    f"{source}: field '{prefix}.affiliations[{j}]' references affiliation "
                    f"{aff_idx} but only {len(affiliations)} affiliation(s) are defined"
                )
            affs.append(aff_idx - 1)  # YAML is 1-based; the IR is 0-based

        email = raw.get("email")
        if email is not None and not isinstance(email, str):
            raise MetaValidationError(f"{source}: field '{prefix}.email' must be a string")

        corresponding = raw.get("corresponding", False)
        if not isinstance(corresponding, bool):
            raise MetaValidationError(f"{source}: field '{prefix}.corresponding' must be a boolean")

        authors.append(
            Author(name=name, affiliations=tuple(affs), email=email, corresponding=corresponding)
        )

    abstract = data.get("abstract", "")
    if not isinstance(abstract, str):
        raise _field_error(source, "abstract", "must be a string")

    raw_keywords = data.get("keywords", [])
    if not isinstance(raw_keywords, list):
        raise _field_error(source, "keywords", "must be a list of strings")
    keywords: list[str] = []
    for i, kw in enumerate(raw_keywords):
        if not isinstance(kw, str) or not kw.strip():
            raise MetaValidationError(f"{source}: field 'keywords[{i}]' must be a non-empty string")
        keywords.append(kw)

    return Meta(
        title=title,
        authors=tuple(authors),
        affiliations=tuple(Affiliation(name=a) for a in affiliations),
        abstract=abstract,
        keywords=tuple(keywords),
    )


# --------------------------------------------------------------------------
# YAML rendering
# --------------------------------------------------------------------------


def _author_to_dict(author: Author) -> dict:
    data: dict = {"name": author.name, "affiliations": [i + 1 for i in author.affiliations]}
    if author.email:
        data["email"] = author.email
    if author.corresponding:
        data["corresponding"] = True
    return data


def render_paper_yaml(meta: Meta, checks: dict[str, list[str]] | None = None) -> str:
    """Render Meta as paper.yaml text, with '# CHECK:' comments for low-confidence fields."""
    checks = checks or {}
    doc = {
        "title": meta.title,
        "authors": [_author_to_dict(a) for a in meta.authors],
        "affiliations": [a.name for a in meta.affiliations],
        "abstract": meta.abstract,
        "keywords": list(meta.keywords),
    }
    base = yaml.safe_dump(
        doc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=88
    )
    lines = base.splitlines()

    # Insert bottom-to-top so already-computed indices for earlier fields stay valid.
    for field_name in reversed(_TOP_LEVEL_FIELD_ORDER):
        messages = checks.get(field_name)
        if not messages:
            continue
        marker = f"{field_name}:"
        insert_at = next(
            (i for i, line in enumerate(lines) if line == marker or line.startswith(marker + " ")),
            None,
        )
        if insert_at is None:
            continue
        lines[insert_at:insert_at] = [f"# CHECK: {msg}" for msg in messages]

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# load / write-once orchestration
# --------------------------------------------------------------------------


def sidecar_path_for(docx_path: Path | str) -> Path:
    return Path(docx_path).with_name(DEFAULT_SIDECAR_NAME)


def load_meta(sidecar_path: Path | str) -> Meta:
    """Parse and validate an existing paper.yaml. Raises MetaValidationError by field."""
    sidecar_path = Path(sidecar_path)
    text = sidecar_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MetaValidationError(f"{sidecar_path.name}: invalid YAML syntax: {exc}") from exc
    return meta_from_yaml_data(data, source=sidecar_path.name)


def load_or_create_meta(docx_path: Path | str, sidecar_path: Path | str | None = None) -> Meta:
    """Load paper.yaml if present (validating it); otherwise guess and write it once.

    Never overwrites an existing sidecar -- once paper.yaml exists it is the
    source of truth for every later run.
    """
    docx_path = Path(docx_path)
    target = Path(sidecar_path) if sidecar_path is not None else sidecar_path_for(docx_path)

    if target.exists():
        return load_meta(target)

    guess = guess_meta(docx_path)
    text = render_paper_yaml(guess.meta, guess.checks)
    if not target.exists():  # re-check right before writing: write-once, never clobber
        target.write_text(text, encoding="utf-8")
    return guess.meta
