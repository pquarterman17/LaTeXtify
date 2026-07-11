"""Crossref REST client for plain-text citation reconstruction (plan item 14).

When a manuscript carries no citation field codes, its bibliography lives only
as typed text. This module queries Crossref's ``/works`` endpoint with
``query.bibliographic=<raw reference text>`` (``rows=3``) and turns each returned
work into a :class:`CrossrefCandidate` that :mod:`latextify.citations.reconcile`
scores against the typed reference.

Politeness / mailto
-------------------
Crossref asks API users to identify themselves with a ``mailto`` so they can be
routed to the faster "polite" pool and contacted about problematic traffic. The
address is configurable three ways, highest precedence first:

1. the ``mailto`` argument to :class:`CrossrefClient` / :func:`resolve_mailto`;
2. the ``LATEXTIFY_CROSSREF_MAILTO`` environment variable;
3. :data:`DEFAULT_MAILTO`, a documented placeholder.

Operators SHOULD override the placeholder with a real address (CLI
``--crossref-mailto`` / ``emit_project(crossref_mailto=...)``). Requests are made
serially -- at the scale of one manuscript's reference list that comfortably
respects Crossref's rate limits without backoff machinery.

Testing
-------
The client accepts an ``httpx`` ``transport``; tests inject
``httpx.MockTransport`` so no real network traffic occurs. The single live test
is marked ``network`` and skips gracefully when offline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from ..model.refs import Name, RefEntry
from .bib import csl_type_to_bibtex

#: Placeholder contact address; override with a real one in production use.
DEFAULT_MAILTO = "latextify@example.com"
_MAILTO_ENV = "LATEXTIFY_CROSSREF_MAILTO"

DEFAULT_BASE_URL = "https://api.crossref.org"
_USER_AGENT_PRODUCT = "LaTeXtify/0.1 (https://github.com/latextify/latextify)"

# Crossref ``type`` values differ from Zotero/CSL ones; map the common ones and
# fall back to the shared CSL table (then ``misc``) for anything unlisted.
_CROSSREF_TO_BIBTEX = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book-chapter": "incollection",
    "reference-entry": "incollection",
    "book": "book",
    "monograph": "book",
    "book-section": "incollection",
    "report": "techreport",
    "dissertation": "phdthesis",
    "posted-content": "misc",  # preprints
    "dataset": "misc",
}


def resolve_mailto(mailto: str | None) -> str:
    """Resolve the Crossref contact address (argument > env var > placeholder)."""
    if mailto:
        return mailto
    env = os.environ.get(_MAILTO_ENV)
    if env:
        return env
    return DEFAULT_MAILTO


def _crossref_type_to_bibtex(crossref_type: str) -> str:
    kind = (crossref_type or "").strip()
    return _CROSSREF_TO_BIBTEX.get(kind) or csl_type_to_bibtex(kind)


@dataclass(frozen=True)
class CrossrefCandidate:
    """One work returned by Crossref, normalized for scoring + BibTeX emission."""

    title: str | None
    authors: tuple[Name, ...]
    year: str | None
    doi: str | None
    container_title: str | None = None
    publisher: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    url: str | None = None
    crossref_type: str = ""

    @property
    def first_author_surname(self) -> str | None:
        for author in self.authors:
            surname = author.family or author.literal
            if surname:
                return surname
        return None

    def to_refentry(self) -> RefEntry:
        """Build a keyless :class:`RefEntry` (key assigned later, in bulk)."""
        return RefEntry(
            key="",
            entry_type=_crossref_type_to_bibtex(self.crossref_type),
            csl_type=self.crossref_type,
            title=self.title,
            authors=self.authors,
            year=self.year,
            container_title=self.container_title,
            publisher=self.publisher,
            volume=self.volume,
            issue=self.issue,
            pages=self.pages,
            doi=self.doi,
            url=self.url,
            source="crossref",
        )


def _first(value: object) -> str | None:
    """First element of a Crossref list field (title/container-title), cleaned."""
    if isinstance(value, list) and value:
        text = str(value[0]).strip()
        return text or None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_authors(raw: object) -> tuple[Name, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[Name] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family", "")).strip()
        given = str(item.get("given", "")).strip()
        literal = str(item.get("name", "")).strip()  # organizational author
        if not (family or given or literal):
            continue
        names.append(Name(family=family, given=given, literal=literal))
    return tuple(names)


def _parse_year(item: dict) -> str | None:
    for key in ("issued", "published-print", "published-online", "published", "created"):
        block = item.get(key)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            match = re.search(r"\d{4}", str(parts[0][0]))
            if match:
                return match.group(0)
    return None


def candidate_from_item(item: dict) -> CrossrefCandidate:
    """Convert one Crossref ``message.items[]`` dict into a candidate."""
    return CrossrefCandidate(
        title=_first(item.get("title")),
        authors=_parse_authors(item.get("author")),
        year=_parse_year(item),
        doi=_clean(item.get("DOI") or item.get("doi")),
        container_title=_first(item.get("container-title")),
        publisher=_clean(item.get("publisher")),
        volume=_clean(item.get("volume")),
        issue=_clean(item.get("issue")),
        pages=_clean(item.get("page")),
        url=_clean(item.get("URL") or item.get("url")),
        crossref_type=str(item.get("type", "")).strip(),
    )


class CrossrefClient:
    """Thin, serial Crossref ``/works`` client used for reference matching."""

    def __init__(
        self,
        *,
        mailto: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.mailto = resolve_mailto(mailto)
        user_agent = f"{_USER_AGENT_PRODUCT} mailto:{self.mailto}"
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    def query_bibliographic(self, text: str, *, rows: int = 3) -> list[CrossrefCandidate]:
        """Return up to ``rows`` candidates for a raw bibliographic reference.

        Sends ``mailto`` as a query parameter too (the polite-pool convention),
        in addition to the User-Agent header. Degrades to an empty list --
        never raises -- for a non-200 response, a network failure/timeout, or a
        malformed (non-JSON) response body, so a single flaky Crossref request
        flags its reference for verification instead of crashing the whole
        reconciliation run (plan item 14's graceful-degradation contract).
        """
        query = (text or "").strip()
        if not query:
            return []
        try:
            response = self._client.get(
                "/works",
                params={"query.bibliographic": query, "rows": rows, "mailto": self.mailto},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return []
        return [candidate_from_item(item) for item in items if isinstance(item, dict)]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CrossrefClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
