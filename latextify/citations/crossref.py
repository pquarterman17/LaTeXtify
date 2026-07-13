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

import html
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


class CrossrefUnavailable(Exception):
    """Crossref could not be reached (network/timeout/5xx) for a DOI lookup.

    Distinct from "the DOI genuinely isn't in Crossref" (a 404, reported as
    ``None``): the validation pass treats this as *unchecked* (offline) rather
    than as a dead DOI, so a dropped connection never masquerades as a bad
    reference.
    """

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
    #: Crossref's abbreviated journal title ("Phys. Rev. B"), when present -- lets
    #: reference validation accept an abbreviated journal name without a false
    #: "wrong journal" flag. Not carried into the .bib.
    short_container_title: str | None = None
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


# Crossref titles carry JATS/MathML markup: "Coupled <mml:math ...><mml:mi>YIG
# </mml:mi><mml:mo>/</mml:mo><mml:mi>Co</mml:mi></mml:math> Heterostructures",
# and inline <i>/<sub>/<sup>/<scp> tags. Left raw, that markup lands verbatim in
# references.bib (an observed "klingler2018spintorque" title). Strip every tag
# (keeping the text between them, so "<mml:mi>YIG</mml:mi>/<mml:mi>Co</mml:mi>"
# -> "YIG/Co"), then decode HTML entities and collapse the whitespace the
# removed multi-line math block leaves behind.
_MARKUP_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_markup(text: str) -> str:
    text = _MARKUP_TAG_RE.sub("", text)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _first(value: object) -> str | None:
    """First element of a Crossref list field (title/container-title), cleaned.

    Markup is stripped (see :func:`_strip_markup`) so JATS/MathML from Crossref
    never reaches ``references.bib``.
    """
    if isinstance(value, list) and value:
        text = _strip_markup(str(value[0]))
        return text or None
    if isinstance(value, str):
        text = _strip_markup(value)
        return text or None
    return None


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)


def _normalize_doi(doi: str | None) -> str:
    """Strip a ``doi:`` / ``https://doi.org/`` prefix and surrounding space."""
    if not doi:
        return ""
    return _DOI_PREFIX_RE.sub("", doi.strip()).strip()


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
        reconciliation run (plan item 14's graceful-degradation contract). Use
        :meth:`query_bibliographic_checked` when you need to tell an outage
        apart from a genuine no-match.
        """
        try:
            return self._query_bibliographic(text, rows=rows)
        except CrossrefUnavailable:
            return []

    def query_bibliographic_checked(
        self, text: str, *, rows: int = 3
    ) -> list[CrossrefCandidate]:
        """Like :meth:`query_bibliographic` but *raises* :class:`CrossrefUnavailable`
        when Crossref is unreachable (network failure, timeout, 5xx, or a
        malformed body), so a caller can record "couldn't check" instead of
        mislabeling the reference. A successful response with no matches still
        returns an empty list. Mirrors :meth:`get_by_doi`'s outage semantics so
        the no-DOI validation path can trip the same offline short-circuit.
        """
        return self._query_bibliographic(text, rows=rows)

    def _query_bibliographic(self, text: str, *, rows: int = 3) -> list[CrossrefCandidate]:
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
        except (httpx.HTTPError, ValueError) as exc:
            raise CrossrefUnavailable(str(exc)) from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return []
        return [candidate_from_item(item) for item in items if isinstance(item, dict)]

    def get_by_doi(self, doi: str) -> CrossrefCandidate | None:
        """Look a DOI up exactly (``/works/{doi}``) for reference validation.

        Returns the canonical work as a :class:`CrossrefCandidate`, or ``None``
        when Crossref has no such DOI (an HTTP 404 -- a typo'd or invalid DOI).
        Raises :class:`CrossrefUnavailable` on a network failure, timeout, or
        server (5xx/other non-200) error, so the caller can tell "this DOI is
        bad" apart from "I couldn't reach Crossref right now". A blank DOI
        returns ``None`` without a request.

        The DOI is sent as the raw path (Crossref expects the bare DOI,
        including its internal ``/``); a ``doi:`` or ``https://doi.org/`` prefix
        is stripped first.
        """
        cleaned = _normalize_doi(doi)
        if not cleaned:
            return None
        try:
            response = self._client.get("/works/" + cleaned, params={"mailto": self.mailto})
        except httpx.HTTPError as exc:
            raise CrossrefUnavailable(str(exc)) from exc
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CrossrefUnavailable(str(exc)) from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            return None
        return candidate_from_item(message)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CrossrefClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
