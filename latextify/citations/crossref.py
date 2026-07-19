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
respects Crossref's rate limits.

Robustness
----------
Crossref returns intermittent 429/5xx responses and the occasional truncated
body even in normal operation, so every request gets bounded retries with
exponential backoff + jitter (429 honors ``Retry-After``, capped). A clean 404
or an empty result set is a real answer and is never retried. On top of that a
simple circuit breaker trips after several *consecutive* failed calls: once
Crossref looks down, further calls fail fast instead of burning one full
retry-and-timeout cycle per reference.

Testing
-------
The client accepts an ``httpx`` ``transport``; tests inject
``httpx.MockTransport`` so no real network traffic occurs. The single live test
is marked ``network`` and skips gracefully when offline.
"""

from __future__ import annotations

import html
import os
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ..model.refs import Name, RefEntry
from .bib import csl_type_to_bibtex

#: Placeholder contact address; override with a real one in production use.
DEFAULT_MAILTO = "latextify@example.com"
_MAILTO_ENV = "LATEXTIFY_CROSSREF_MAILTO"

DEFAULT_BASE_URL = "https://api.crossref.org"
_USER_AGENT_PRODUCT = "LaTeXtify/0.1 (https://github.com/latextify/latextify)"

#: Retries after the first attempt for *transient* failures only (connect/read
#: errors, 429, 5xx, malformed JSON body). A 404 / empty result is a real
#: answer, never retried.
DEFAULT_MAX_RETRIES = 2
#: Base backoff in seconds; retry N sleeps ~ base * 2**(N-1) (+ up to 25%
#: jitter). Tests pass ``retry_backoff=0.0`` so error paths stay instant.
DEFAULT_RETRY_BACKOFF = 1.0
#: Hard cap on any single inter-attempt sleep, including a server-sent
#: ``Retry-After`` -- a GUI request must never hang for a minute on one ref.
_MAX_BACKOFF = 10.0
#: After this many consecutive failed *calls* (each already retried), the
#: client fails fast without touching the network: Crossref is treated as down
#: for the rest of the run. Any successful call closes the breaker again.
_BREAKER_THRESHOLD = 3


def _retry_delay(attempt: int, retry_after: str | None, backoff: float) -> float:
    """Seconds to sleep before retry ``attempt`` (1-based), capped at ``_MAX_BACKOFF``.

    A numeric ``Retry-After`` (the 429 convention) wins over the computed
    exponential backoff; an HTTP-date or garbage value falls through to it.
    """
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), _MAX_BACKOFF)
        except ValueError:
            pass
    delay = backoff * (2 ** (attempt - 1))
    return min(delay + random.uniform(0.0, delay / 4.0), _MAX_BACKOFF)


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


# Crossref titles carry JATS/MathML markup: e.g. "Coupled <mml:math ...><mml:mi>Ax
# </mml:mi><mml:mo>/</mml:mo><mml:mi>Bz</mml:mi></mml:math> Heterostructures",
# and inline <i>/<sub>/<sup>/<scp> tags. Left raw, that markup lands verbatim in
# references.bib. Strip every tag (keeping the text between them, so
# "<mml:mi>Ax</mml:mi>/<mml:mi>Bz</mml:mi>" -> "Ax/Bz"), then decode HTML
# entities and collapse the whitespace the removed multi-line math block leaves behind.
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
        timeout: float = 10.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ) -> None:
        self.mailto = resolve_mailto(mailto)
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self._consecutive_failures = 0
        user_agent = f"{_USER_AGENT_PRODUCT} mailto:{self.mailto}"
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    def _get_json(self, path: str, params: dict) -> dict | None:
        """GET ``path`` and parse the JSON body, with bounded retries.

        Returns the parsed payload dict, or ``None`` for a 404 (a real "not
        found", never retried). Transient failures -- transport errors, 429
        (honoring ``Retry-After``), 5xx, and a malformed/truncated body -- are
        retried with backoff; exhausting the retries, or any non-transient
        failure, raises :class:`CrossrefUnavailable`.

        The circuit breaker wraps every call: once ``_BREAKER_THRESHOLD``
        consecutive calls have failed, further calls raise immediately so a
        full outage costs a few timeouts, not one per remaining reference.
        """
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            raise CrossrefUnavailable(
                f"skipped after {self._consecutive_failures} consecutive Crossref failures"
            )
        try:
            payload = self._get_json_retrying(path, params)
        except CrossrefUnavailable:
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return payload

    def _get_json_retrying(self, path: str, params: dict) -> dict | None:
        last_error = "unknown error"
        retry_after: str | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(_retry_delay(attempt, retry_after, self.retry_backoff))
                retry_after = None
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:  # connect/read/write trouble: transient
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            except httpx.HTTPError as exc:  # redirect loops etc.: not transient
                raise CrossrefUnavailable(str(exc)) from exc
            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                last_error = f"HTTP {response.status_code}"
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # remaining 4xx: not transient
                raise CrossrefUnavailable(str(exc)) from exc
            try:
                payload = response.json()
            except ValueError:  # malformed/truncated body: usually transient
                last_error = "malformed JSON body"
                continue
            return payload if isinstance(payload, dict) else {}
        raise CrossrefUnavailable(
            f"gave up after {self.max_retries + 1} attempt(s); last error: {last_error}"
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
        payload = self._get_json(
            "/works",
            {"query.bibliographic": query, "rows": rows, "mailto": self.mailto},
        )
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

        The DOI is percent-encoded into the path (``/`` kept literal, as
        Crossref expects); a ``doi:`` or ``https://doi.org/`` prefix is
        stripped first. Encoding matters: real DOIs contain ``<``, ``>``,
        ``;``, ``#`` (the SICI-era Wiley style) and a raw ``#`` or ``?`` would
        otherwise truncate the request path into a fragment/query.
        """
        cleaned = _normalize_doi(doi)
        if not cleaned:
            return None
        payload = self._get_json(
            "/works/" + quote(cleaned, safe="/"), {"mailto": self.mailto}
        )
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
