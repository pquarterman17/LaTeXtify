"""Crossref client + candidate scoring/reconciliation (plan item 14).

The client is exercised against ``httpx.MockTransport`` so no real network
traffic occurs. One live test is marked ``network`` and skips gracefully when
Crossref is unreachable.
"""

from __future__ import annotations

import httpx
import pytest

from latextify.citations import crossref, reconcile
from latextify.citations.crossref import (
    DEFAULT_MAILTO,
    CrossrefCandidate,
    CrossrefClient,
    candidate_from_item,
    resolve_mailto,
)
from latextify.citations.reconcile import (
    ReferenceItem,
    best_candidate,
    raw_refentry,
    reconcile_references,
    score_candidate,
)
from latextify.model.refs import Name

# --------------------------------------------------------------------------- #
# mailto resolution
# --------------------------------------------------------------------------- #


def test_resolve_mailto_prefers_argument(monkeypatch):
    monkeypatch.setenv("LATEXTIFY_CROSSREF_MAILTO", "env@example.org")
    assert resolve_mailto("arg@example.org") == "arg@example.org"


def test_resolve_mailto_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("LATEXTIFY_CROSSREF_MAILTO", "env@example.org")
    assert resolve_mailto(None) == "env@example.org"


def test_resolve_mailto_defaults_to_placeholder(monkeypatch):
    monkeypatch.delenv("LATEXTIFY_CROSSREF_MAILTO", raising=False)
    assert resolve_mailto(None) == DEFAULT_MAILTO


# --------------------------------------------------------------------------- #
# candidate parsing + client requests
# --------------------------------------------------------------------------- #


def _work(**over):
    item = {
        "title": ["A Fine Paper on Widgets"],
        "author": [
            {"given": "Ada", "family": "Lovelace", "sequence": "first"},
            {"given": "Charles", "family": "Babbage"},
        ],
        "issued": {"date-parts": [[1998, 5]]},
        "DOI": "10.1000/widgets.1998",
        "container-title": ["Journal of Widgets"],
        "volume": "12",
        "issue": "3",
        "page": "45-67",
        "type": "journal-article",
        "URL": "https://doi.org/10.1000/widgets.1998",
    }
    item.update(over)
    return item


def test_candidate_from_item_parses_fields():
    cand = candidate_from_item(_work())
    assert cand.title == "A Fine Paper on Widgets"
    assert cand.first_author_surname == "Lovelace"
    assert cand.year == "1998"
    assert cand.doi == "10.1000/widgets.1998"
    assert cand.container_title == "Journal of Widgets"
    assert cand.pages == "45-67"


def test_candidate_to_refentry_maps_type():
    entry = candidate_from_item(_work()).to_refentry()
    assert entry.entry_type == "article"
    assert entry.source == "crossref"
    assert entry.authors[0].family == "Lovelace"
    entry_chapter = candidate_from_item(_work(type="book-chapter")).to_refentry()
    assert entry_chapter.entry_type == "incollection"


def test_organizational_author_becomes_literal_name():
    cand = candidate_from_item(_work(author=[{"name": "The MoEDAL Collaboration"}]))
    assert cand.authors[0].literal == "The MoEDAL Collaboration"


def _client_capturing(captured: list[httpx.Request], items=None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"message": {"items": items or [_work()]}})

    return CrossrefClient(mailto="me@example.org", transport=httpx.MockTransport(handler))


def test_query_bibliographic_sends_expected_params_and_user_agent():
    captured: list[httpx.Request] = []
    client = _client_capturing(captured)
    candidates = client.query_bibliographic("A Fine Paper on Widgets, Lovelace 1998", rows=3)

    assert len(candidates) == 1
    request = captured[0]
    assert request.url.path == "/works"
    assert request.url.params["query.bibliographic"].startswith("A Fine Paper")
    assert request.url.params["rows"] == "3"
    assert request.url.params["mailto"] == "me@example.org"
    assert "mailto:me@example.org" in request.headers["User-Agent"]
    client.close()


def test_query_bibliographic_empty_text_makes_no_request():
    captured: list[httpx.Request] = []
    client = _client_capturing(captured)
    assert client.query_bibliographic("   ") == []
    assert captured == []
    client.close()


def test_client_context_manager_closes():
    with _client_capturing([]) as client:
        assert client.query_bibliographic("x")


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

_REF_TEXT = "Lovelace, A., Babbage, C. A Fine Paper on Widgets. Journal of Widgets 12, 45 (1998)."


def test_score_high_for_matching_candidate():
    cand = candidate_from_item(_work())
    assert score_candidate(_REF_TEXT, cand) > 0.9


def test_score_low_for_wrong_candidate():
    wrong = CrossrefCandidate(
        title="Entirely Unrelated Treatise on Llamas",
        authors=(Name(family="Zzyzx"),),
        year="1850",
        doi="10.9999/nope",
    )
    assert score_candidate(_REF_TEXT, wrong) < 0.5


def test_year_and_author_corroborate_score():
    # Same title, but neither the year nor the surname appears in the reference.
    partial = CrossrefCandidate(
        title="A Fine Paper on Widgets", authors=(Name(family="Nobody"),), year="1600", doi="x"
    )
    full = candidate_from_item(_work())
    assert score_candidate(_REF_TEXT, full) > score_candidate(_REF_TEXT, partial)


def test_best_candidate_picks_highest():
    good = candidate_from_item(_work())
    bad = CrossrefCandidate(title="Llamas", authors=(), year=None, doi="y")
    chosen, score = best_candidate(_REF_TEXT, [bad, good])
    assert chosen is good
    assert score > 0.9


def test_best_candidate_empty_list():
    chosen, score = best_candidate(_REF_TEXT, [])
    assert chosen is None
    assert score == 0.0


# --------------------------------------------------------------------------- #
# raw fallback + reconcile
# --------------------------------------------------------------------------- #


def test_raw_refentry_parses_surname_and_year():
    entry = raw_refentry("Foster, G. Some unpublished notes, internal memo, 2016.")
    assert entry.entry_type == "misc"
    assert entry.source == "raw"
    assert entry.authors[0].family == "Foster"
    assert entry.year == "2016"
    assert entry.title.startswith("Foster")


def _mock_client(mapping: dict[str, dict]):
    """A client whose response depends on a keyword found in the query."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query.bibliographic", "").lower()
        for keyword, item in mapping.items():
            if keyword in query:
                return httpx.Response(200, json={"message": {"items": [item]}})
        return httpx.Response(200, json={"message": {"items": []}})

    return CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler))


def test_reconcile_accepts_match_and_flags_miss():
    mapping = {
        "widgets": _work(),
    }
    references = [
        ReferenceItem(text=_REF_TEXT, number=1),
        ReferenceItem(text="Obscure, Q. Untraceable manuscript, 2099.", number=2),
    ]
    with _mock_client(mapping) as client:
        outcome = reconcile_references(references, client)

    assert len(outcome.entries) == 2
    matched, flagged = outcome.records
    assert matched.matched is True
    assert matched.source == "crossref"
    assert matched.doi == "10.1000/widgets.1998"
    assert matched.verify is False
    assert matched.ref_number == 1

    assert flagged.matched is False
    assert flagged.source == "raw"
    assert flagged.doi is None
    assert flagged.verify is True
    assert flagged.ref_number == 2
    # Keys are assigned across the whole list and are unique.
    assert matched.key != flagged.key
    assert all(e.key for e in outcome.entries)


def test_reconcile_below_threshold_is_flagged_even_with_candidate():
    # A returned candidate that scores under the threshold must still be flagged.
    weak = _work(title=["Totally Different Subject"], author=[{"family": "Nobody"}])
    references = [ReferenceItem(text=_REF_TEXT, number=1)]
    with _mock_client({"widgets": weak}) as client:
        outcome = reconcile_references(references, client, threshold=0.9)
    assert outcome.records[0].verify is True
    assert outcome.records[0].source == "raw"


# --------------------------------------------------------------------------- #
# live network (opt-in; skips offline)
# --------------------------------------------------------------------------- #


@pytest.mark.network
def test_live_crossref_returns_a_doi():
    reference = (
        "Novoselov, K. S. et al. Electric field effect in atomically thin carbon "
        "films. Science 306, 666-669 (2004)."
    )
    try:
        with crossref.CrossrefClient(mailto="latextify-test@example.com") as client:
            candidates = client.query_bibliographic(reference, rows=3)
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        pytest.skip(f"Crossref unreachable: {exc}")

    assert candidates, "expected at least one Crossref candidate"
    best, score = reconcile.best_candidate(reference, candidates)
    assert best is not None
    assert best.doi  # a real match carries a DOI
    assert score >= reconcile.DEFAULT_THRESHOLD
