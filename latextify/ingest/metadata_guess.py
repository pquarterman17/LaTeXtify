"""paper.yaml metadata sidecar: guess orchestration, write-once I/O.

Plan item 8. :func:`guess_meta` scans the first ``max_paragraphs`` paragraphs
of ``word/document.xml`` (read by :mod:`latextify.ingest.metadata_paragraphs`)
and ties together the field-by-field heuristics that live in
:mod:`latextify.ingest.metadata_authors` (title/authors/affiliations) and
:mod:`latextify.ingest.metadata_body` (abstract/keywords/corresponding
email) into one :class:`MetaGuess`. :func:`front_matter_span` re-runs the
SAME detection sequence to find the paragraph range consumed by the title
page, so :mod:`latextify.ingest.frontmatter` can strip it out of the body
before pandoc converts it -- without stripping, the compiled PDF would show
the title page twice.

Every heuristic is conservative: whenever a guess is not well supported by
the document (no Title style found, no affiliation markers, no Abstract
heading, ...) the guess is still made on a best-effort basis but the field is
recorded in the returned ``MetaGuess.checks`` mapping, which
:func:`latextify.ingest.metadata_schema.render_paper_yaml` turns into
``# CHECK:`` comments in the emitted file. Nothing is ever silently
confident.

``paper.yaml`` is written only if absent (write-once, in
:func:`load_or_create_meta`). Once it exists it is the source of truth:
:func:`load_meta` parses and validates it via
:func:`latextify.ingest.metadata_schema.meta_from_yaml_data`, raising
``MetaValidationError`` naming the offending field on any schema violation.

IR convention: ``Author.affiliations`` are 0-based indices into
``Meta.affiliations``; the paper.yaml FILE stays 1-based -- see
:mod:`latextify.ingest.metadata_schema` for where that conversion happens.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from latextify.ingest.metadata_authors import (
    guess_affiliations,
    guess_authors,
    guess_title,
    link_author_affiliations,
)
from latextify.ingest.metadata_body import (
    abstract_heading_index,
    find_corresponding_email,
    guess_abstract,
    guess_keywords,
    keywords_line_index,
    title_page_end_index,
)
from latextify.ingest.metadata_paragraphs import extract_paragraphs, read_document_root
from latextify.ingest.metadata_schema import (
    DEFAULT_SIDECAR_NAME,
    MetaValidationError,
    meta_from_yaml_data,
    render_paper_yaml,
)
from latextify.model.meta import Affiliation, Meta

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
    root = read_document_root(Path(docx_path))
    paras = extract_paragraphs(root, max_paragraphs)

    title, title_idx, title_checks = guess_title(paras)
    author_result = guess_authors(paras, max(title_idx + 1, 0))
    affiliation_entries, aff_end_idx, aff_checks = guess_affiliations(
        paras, author_result.next_idx, author_result.expected_affiliation_count
    )
    affiliations = [e.text for e in affiliation_entries]
    abstract, abstract_end_idx, abstract_checks = guess_abstract(paras, aff_end_idx)
    keywords, keyword_checks = guess_keywords(paras, abstract_end_idx)

    authors = list(author_result.authors)
    author_checks = list(author_result.checks)

    if author_result.expected_affiliation_count:
        per_author_affs, link_checks = link_author_affiliations(
            author_result.raw_markers, author_result.marker_first_seen_order, affiliation_entries
        )
        aff_checks.extend(link_checks)
        authors = [
            replace(author, affiliations=affs)
            for author, affs in zip(authors, per_author_affs, strict=True)
        ]

    # Affiliation indices on each Author come from markers seen on the author
    # line, but guess_affiliations may come up short of matching paragraphs
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
        email = find_corresponding_email(paras[: title_page_end_index(paras)])
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
# front-matter span (for stripping the title page out of the body)
# --------------------------------------------------------------------------


def front_matter_span(docx_path: Path | str, *, max_paragraphs: int = 20) -> tuple[int, int] | None:
    """Paragraph span ``[start, end)`` occupied by the manuscript's title page.

    A journal's metadata template re-renders title / authors / affiliations /
    corresponding line / abstract / keywords from ``paper.yaml``. Pandoc,
    meanwhile, converts the manuscript body verbatim -- so without stripping,
    the manuscript's own title page appears in the body too and the compiled
    PDF shows all of it TWICE. :func:`latextify.ingest.frontmatter.strip_front_matter_from_docx`
    removes this span before pandoc runs.

    This runs the SAME detection sequence as :func:`guess_meta` (so the span
    removed from the body matches exactly what is re-rendered as metadata --
    no duplication, no content loss) but returns only the consumed paragraph
    range. Because it re-detects from the docx directly, it works whether or
    not a ``paper.yaml`` sidecar already exists (``load_or_create_meta`` skips
    guessing once the sidecar is written, but the stripping decision still
    needs the docx-side spans).

    Conservative gate -- returns ``None`` (strip nothing) unless there is a
    STRONG title-page signal: an author line carrying affiliation markers, or
    an "Abstract" heading. A bare Title-styled heading alone is NOT enough,
    because the paragraph after it may be ordinary body text rather than an
    author line (that is exactly what separates a real title page from a
    stray top-of-document heading, e.g. a figures-only fixture). Indices are
    0-based into the top-level ``w:p`` children of ``w:body``, matching
    :func:`~latextify.ingest.metadata_paragraphs.extract_paragraphs` and the
    stripping mechanism.
    """
    root = read_document_root(Path(docx_path))
    paras = extract_paragraphs(root, max_paragraphs)

    _, title_idx, _ = guess_title(paras)
    if title_idx < 0:
        return None

    author_result = guess_authors(paras, title_idx + 1)
    _, aff_end_idx, _ = guess_affiliations(
        paras, author_result.next_idx, author_result.expected_affiliation_count
    )

    has_markers = author_result.expected_affiliation_count > 0
    has_abstract = abstract_heading_index(paras, aff_end_idx) is not None
    if not (has_markers or has_abstract):
        return None

    # End = the furthest paragraph guess_meta itself consumes, so the stripped
    # span equals the rendered metadata exactly. guess_abstract returns
    # aff_end_idx unchanged when there is no heading, so this stays bounded by
    # the real structure (author markers cap affiliations; an Abstract heading
    # / Keywords line cap the rest).
    end = title_idx + 1
    if author_result.authors:
        end = max(end, author_result.next_idx, aff_end_idx)
    _, abstract_end_idx, _ = guess_abstract(paras, aff_end_idx)
    end = max(end, abstract_end_idx)
    kw_idx = keywords_line_index(paras, abstract_end_idx)
    if kw_idx is not None:
        end = max(end, kw_idx + 1)

    if end <= title_idx:
        return None
    return (title_idx, end)


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

    from .metadata_guess_nondocx import guess_meta_dispatch

    guess = guess_meta_dispatch(docx_path)
    text = render_paper_yaml(guess.meta, guess.checks)
    if not target.exists():  # re-check right before writing: write-once, never clobber
        target.write_text(text, encoding="utf-8")
    return guess.meta
