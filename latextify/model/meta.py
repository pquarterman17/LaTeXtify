"""Title-page metadata IR — the ``paper.yaml`` schema as frozen dataclasses.

This is the canonical ``Meta`` used both by the metadata sidecar (plan item 8,
which parses it out of the docx) and by the journal templates (plan item 4,
which render ``\\author``/``\\affiliation`` macros from it). Templates render
from these types ONLY — never from raw docx or ad-hoc dicts.

Schema (mirrors the ``paper.yaml`` sidecar):

    title:        str
    authors:      list of Author
    affiliations: list of Affiliation, referenced by index from each Author
    abstract:     str
    keywords:     list of str

Authors carry *indices* into the shared ``affiliations`` list rather than
inlined affiliation objects, so a single institution is written once and
shared — which is exactly what the grouped author-block macros of every
journal family need.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Affiliation:
    """One institution / address block, referenced by index from ``Author``.

    ``name`` is the full affiliation string as it should appear in the
    author block, e.g. ``"Department of Physics, University A, City, Country"``.
    Structured journals (elsarticle's ``organization=``/``city=``/``country=``)
    can parse this string in their own metadata template; the IR keeps a single
    canonical field so every journal renders from the same data.
    """

    name: str


@dataclass(frozen=True)
class Author:
    """One author. ``affiliations`` are indices into ``Meta.affiliations``."""

    name: str
    affiliations: tuple[int, ...] = ()
    email: str | None = None
    corresponding: bool = False


@dataclass(frozen=True)
class Meta:
    """Title-page metadata for one manuscript."""

    title: str
    authors: tuple[Author, ...] = ()
    affiliations: tuple[Affiliation, ...] = ()
    abstract: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
