"""Author-block grouping helpers exposed to every journal's Jinja templates.

Journal author blocks are not flat lists — they group authors by shared
affiliation. REVTeX and elsarticle list a run of consecutive authors and then
the affiliation(s) they share; the metadata templates call
``group_authors(meta.authors)`` (registered as a Jinja global by the loader)
to get those runs without reimplementing the loop in Jinja.

A later journal that groups differently (e.g. IEEEtran groups by affiliation
*set* regardless of document order — plan item 11) adds its own helper here and
registers it the same way; the grouping strategy stays out of the templates.
"""

from __future__ import annotations

from dataclasses import dataclass

from latextify.model.meta import Author


@dataclass(frozen=True)
class AuthorGroup:
    """A run of authors that share the same set of affiliation indices."""

    authors: tuple[Author, ...]
    affiliations: tuple[int, ...]


def group_consecutive_by_affiliation(authors: tuple[Author, ...]) -> list[AuthorGroup]:
    """Group *consecutive* authors sharing an identical affiliation-index tuple.

    This is the REVTeX / elsarticle convention: authors are emitted in document
    order, and a block of adjacent authors with the same affiliations is written
    as consecutive ``\\author`` lines followed by the shared ``\\affiliation``
    line(s) once. Order is preserved; only adjacent duplicates collapse.

    >>> from latextify.model.meta import Author
    >>> a = Author("Alice", (0, 1))
    >>> b = Author("Bob", (0, 1))
    >>> c = Author("Carol", (1,))
    >>> [ (tuple(g.authors[i].name for i in range(len(g.authors))), g.affiliations)
    ...   for g in group_consecutive_by_affiliation((a, b, c)) ]
    [(('Alice', 'Bob'), (0, 1)), (('Carol',), (1,))]
    """
    authors = tuple(authors)
    groups: list[AuthorGroup] = []
    i = 0
    while i < len(authors):
        key = authors[i].affiliations
        j = i + 1
        while j < len(authors) and authors[j].affiliations == key:
            j += 1
        groups.append(AuthorGroup(authors[i:j], key))
        i = j
    return groups


def group_globally_by_affiliation(authors: tuple[Author, ...]) -> list[AuthorGroup]:
    """Group *all* authors sharing an identical affiliation-index tuple, globally.

    This is the IEEEtran convention (plan item 11): ``\\IEEEauthorblockN{}`` /
    ``\\IEEEauthorblockA{}`` blocks are keyed by affiliation set regardless of
    where in the author list a match occurs -- unlike REVTeX/elsarticle's
    :func:`group_consecutive_by_affiliation`, non-adjacent authors sharing an
    affiliation are merged into one block. Each author appears in exactly one
    group; groups are ordered by the first appearance of their affiliation key
    in the input; authors within a group keep their relative document order.

    >>> from latextify.model.meta import Author
    >>> a = Author("Alice", (0,))
    >>> b = Author("Bob", (1,))
    >>> c = Author("Carol", (0,))
    >>> [ (tuple(au.name for au in g.authors), g.affiliations)
    ...   for g in group_globally_by_affiliation((a, b, c)) ]
    [(('Alice', 'Carol'), (0,)), (('Bob',), (1,))]
    """
    authors = tuple(authors)
    order: list[tuple[int, ...]] = []
    buckets: dict[tuple[int, ...], list[Author]] = {}
    for author in authors:
        key = author.affiliations
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(author)
    return [AuthorGroup(tuple(buckets[key]), key) for key in order]
