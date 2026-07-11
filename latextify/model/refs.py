"""Citation-related intermediate representation.

Frozen dataclasses shared across the citation extraction stage (plan item 7)
and the emitter. ``RefEntry`` is one bibliography entry with CSL-shaped fields;
``Citation`` is one in-text citation, carrying its document-order index (which
pairs with the body pipeline's ``%%CITE:<idx>%%`` anchors) and the resolved
BibTeX keys it should render as ``\\cite{...}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Name:
    """A single author/contributor name in CSL shape.

    ``family``/``given`` are personal-name parts; ``literal`` holds an
    institutional or otherwise non-decomposable name ("CERN", "The MoEDAL
    Collaboration"). When ``literal`` is set and ``family`` is empty the name
    is treated as institutional and brace-protected on BibTeX emission.
    """

    family: str = ""
    given: str = ""
    literal: str = ""

    @property
    def is_literal(self) -> bool:
        return bool(self.literal) and not self.family


@dataclass(frozen=True)
class RefEntry:
    """One bibliography entry, normalized from any citation source.

    ``key`` is the BibTeX citation key; it is assigned late (after the whole
    document is scanned) so collisions can be de-conflicted with a/b/c
    suffixes. ``entry_type`` is the BibTeX type ("article", "inproceedings",
    ...); ``csl_type`` preserves the original CSL type for provenance.
    ``raw_id`` is the source's own item id, used for de-duplication when no DOI
    is present.
    """

    key: str
    entry_type: str
    csl_type: str = ""
    title: str | None = None
    authors: tuple[Name, ...] = ()
    year: str | None = None
    container_title: str | None = None
    publisher: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    url: str | None = None
    isbn: str | None = None
    source: str = ""
    raw_id: str | None = None


@dataclass(frozen=True)
class Citation:
    """One in-text citation in document order.

    ``index`` is the 0-based position among all in-text citations and pairs
    with the body pipeline's ``%%CITE:<index>%%`` anchor. ``keys`` are the
    resolved BibTeX keys (a multi-item citation resolves to several keys) in
    the order they appeared. ``source`` records which extractor produced it
    ("zotero", "mendeley", ...).
    """

    index: int
    keys: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""
