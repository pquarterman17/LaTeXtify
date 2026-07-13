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
    CrossrefUnavailable,
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


def test_candidate_title_strips_mathml_and_jats_markup():
    # Crossref returns titles carrying MathML / JATS inline tags; none of it may
    # reach references.bib (the observed klingler2018spintorque "YIG/Co" title).
    mathml = (
        "Spin-Torque Excitation of Coupled "
        '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML" display="inline">'
        "<mml:mrow><mml:mi>YIG</mml:mi><mml:mo>/</mml:mo><mml:mi>Co</mml:mi></mml:mrow>"
        "</mml:math>\n Heterostructures"
    )
    cand = candidate_from_item(_work(title=[mathml]))
    assert cand.title == "Spin-Torque Excitation of Coupled YIG/Co Heterostructures"
    assert "<" not in cand.title and "mml" not in cand.title


def test_candidate_title_decodes_html_entities():
    cand = candidate_from_item(_work(title=["Films with <i>T</i> &lt; T&#x2009;<sub>c</sub>"]))
    assert cand.title == "Films with T < T c"


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


def test_query_bibliographic_degrades_on_non_200_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler)) as client:
        # Must degrade to "no candidates", never raise HTTPStatusError -- the
        # plan's documented graceful-degradation contract for reconciliation.
        assert client.query_bibliographic("Some reference text") == []


def test_query_bibliographic_degrades_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    with CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler)) as client:
        assert client.query_bibliographic("Some reference text") == []


def test_query_bibliographic_degrades_on_network_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler)) as client:
        assert client.query_bibliographic("Some reference text") == []


def test_reconcile_survives_crossref_server_error(monkeypatch):
    """A Crossref outage must fall back every reference to raw/verify, not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    references = [ReferenceItem(text=_REF_TEXT, number=1)]
    with CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler)) as client:
        outcome = reconcile_references(references, client)

    assert len(outcome.entries) == 1
    assert outcome.records[0].matched is False
    assert outcome.records[0].source == "raw"
    assert outcome.records[0].verify is True


def test_client_context_manager_closes():
    with _client_capturing([]) as client:
        assert client.query_bibliographic("x")


# --------------------------------------------------------------------------- #
# get_by_doi (exact DOI lookup for reference validation)
# --------------------------------------------------------------------------- #


def _doi_client(handler):
    return CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler))


def test_get_by_doi_returns_candidate_on_200():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # /works/{doi} returns a single work under "message", not an items list.
        return httpx.Response(200, json={"message": _work()})

    with _doi_client(handler) as client:
        cand = client.get_by_doi("10.1000/widgets.1998")

    assert cand is not None
    assert cand.doi == "10.1000/widgets.1998"
    assert cand.title == "A Fine Paper on Widgets"
    assert captured[0].url.path == "/works/10.1000/widgets.1998"


def test_get_by_doi_strips_url_and_doi_prefixes():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"message": _work()})

    with _doi_client(handler) as client:
        client.get_by_doi("https://doi.org/10.1000/widgets.1998")
        client.get_by_doi("doi:10.1000/widgets.1998")

    assert captured[0].url.path == "/works/10.1000/widgets.1998"
    assert captured[1].url.path == "/works/10.1000/widgets.1998"


def test_get_by_doi_returns_none_on_404():
    with _doi_client(lambda r: httpx.Response(404, text="Not Found")) as client:
        assert client.get_by_doi("10.9999/nope") is None


def test_get_by_doi_blank_makes_no_request():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"message": _work()})

    with _doi_client(handler) as client:
        assert client.get_by_doi("   ") is None
    assert captured == []


def test_get_by_doi_raises_unavailable_on_server_error():
    with _doi_client(lambda r: httpx.Response(503, text="down")) as client:
        with pytest.raises(CrossrefUnavailable):
            client.get_by_doi("10.1000/widgets.1998")


def test_get_by_doi_raises_unavailable_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with _doi_client(handler) as client:
        with pytest.raises(CrossrefUnavailable):
            client.get_by_doi("10.1000/widgets.1998")


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


def test_raw_refentry_surname_skips_leading_initials():
    # An initials-first name ("L. J. Cornelissen") must yield the real surname,
    # not the initial -- the surname seeds the cite-key, and a degenerate
    # first-initial key (every "B." author -> "b20xx") makes apsrev4-2 render a
    # stray "()" disambiguation marker on colliding keys.
    assert raw_refentry("L. J. Cornelissen, J. Liu, Nature Physics (2015).").authors[0].family == (
        "Cornelissen"
    )
    assert raw_refentry("B. L. Giles, Z. Yang, Phys. Rev. B (2015).").authors[0].family == "Giles"
    # The name particle "v." in "A. v. Chumak" is skipped like an initial.
    assert raw_refentry("A. v. Chumak, Magnon Spintronics (2015).").authors[0].family == "Chumak"


def test_raw_refentry_keys_do_not_collide_on_initials():
    # Two different first authors who share a first initial must NOT collapse to
    # the same key stem (the root of the "()" bibliography artifact).
    a = raw_refentry("B. L. Giles, A paper, Phys. Rev. B 92, 1 (2015).")
    b = raw_refentry("D. Gilbert, Another paper, Phys. Rev. B 96, 2 (2016).")
    from latextify.citations.bib import make_base_key

    assert make_base_key(a) != make_base_key(b)
    assert make_base_key(a).startswith("giles")
    assert make_base_key(b).startswith("gilbert")


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
