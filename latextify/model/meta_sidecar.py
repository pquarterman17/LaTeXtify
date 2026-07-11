"""Title-page metadata IR — the ``paper.yaml`` schema (plan item 8).

Mirrors the schema described in ``plans/LATEXTIFY_PLAN.md`` item 8 exactly:

    title: str
    authors: [{name, affiliations: [int], email?, corresponding?: bool}]
    affiliations: [str]
    abstract: str
    keywords: [str]

Lives in a dedicated module (not ``model/meta.py``) because plan item 4 is
concurrently defining the canonical ``Meta``/``Author``/``Affiliation``
dataclasses used by the template registry. Keeping this module minimal and
schema-faithful means the two can be unified mechanically once both land.

Frozen dataclasses only — no behavior, no I/O (see ``model/__init__.py``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Author:
    """One author line entry.

    ``affiliations`` holds 1-based indices into ``Meta.affiliations`` (index
    1 == the first affiliation), matching how they read in the emitted YAML.
    """

    name: str
    affiliations: tuple[int, ...] = ()
    email: str | None = None
    corresponding: bool = False


@dataclass(frozen=True)
class Meta:
    """Title-page metadata consumed by the emitter's metadata templates."""

    title: str
    authors: tuple[Author, ...] = ()
    affiliations: tuple[str, ...] = ()
    abstract: str = ""
    keywords: tuple[str, ...] = ()
