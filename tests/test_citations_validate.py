"""Online reference validation against Crossref (``--check-references``).

Every case runs against ``httpx.MockTransport`` -- no real network. The mock
routes ``/works/{doi}`` (exact DOI lookup -> single ``message``) and ``/works``
(bibliographic query -> ``message.items``) so both validation paths (DOI-bearing
and DOI-less) are exercised offline.
"""

from __future__ import annotations

import httpx

from latextify.citations.crossref import CrossrefClient
from latextify.citations.validate import (
    compare_entry_to_candidate,
    validate_entry,
    validate_references,
)
from latextify.model.refs import Name, RefEntry


def _work(**over):
    item = {
        "title": ["A Fine Paper on Widgets"],
        "author": [
            {"given": "Ada", "family": "Lovelace"},
            {"given": "Charles", "family": "Babbage"},
        ],
        "issued": {"date-parts": [[1998, 5]]},
        "DOI": "10.1000/widgets.1998",
        "container-title": ["Journal of Widgets"],
        "short-container-title": ["J. Widgets"],
        "volume": "12",
        "issue": "3",
        "page": "45-67",
        "type": "journal-article",
    }
    item.update(over)
    return item


def _entry(**over):
    fields = {
        "key": "lovelace1998widgets",
        "entry_type": "article",
        "title": "A Fine Paper on Widgets",
        "authors": (Name(family="Lovelace", given="Ada"), Name(family="Babbage", given="Charles")),
        "year": "1998",
        "container_title": "Journal of Widgets",
        "volume": "12",
        "issue": "3",
        "pages": "45-67",
        "doi": "10.1000/widgets.1998",
    }
    fields.update(over)
    return RefEntry(**fields)


def _client(*, by_doi=None, by_query=None, doi_status=200, query_status=200):
    """Mock client. ``by_doi``: DOI->work. ``by_query``: keyword->work list.

    ``query_status`` != 200 simulates a Crossref outage on the bibliographic
    query path (the no-DOI validation path), distinct from ``doi_status``.
    """
    by_doi = by_doi or {}
    by_query = by_query or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/works/"):
            doi = path[len("/works/") :]
            if doi in by_doi:
                return httpx.Response(doi_status, json={"message": by_doi[doi]})
            return httpx.Response(404, text="Not Found")
        if query_status != 200:
            return httpx.Response(query_status, text="Service Unavailable")
        query = request.url.params.get("query.bibliographic", "").lower()
        for keyword, items in by_query.items():
            if keyword in query:
                return httpx.Response(200, json={"message": {"items": items}})
        return httpx.Response(200, json={"message": {"items": []}})

    return CrossrefClient(mailto="t@e.org", transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# field comparison
# --------------------------------------------------------------------------- #


def test_compare_all_fields_match():
    from latextify.citations.crossref import candidate_from_item

    checks = compare_entry_to_candidate(_entry(), candidate_from_item(_work()))
    assert {c.field for c in checks} == {"title", "authors", "year", "journal", "volume", "issue",
                                         "pages"}
    assert all(c.ok for c in checks)


def test_compare_flags_wrong_year():
    from latextify.citations.crossref import candidate_from_item

    checks = compare_entry_to_candidate(_entry(year="1999"), candidate_from_item(_work()))
    year = next(c for c in checks if c.field == "year")
    assert year.ok is False
    assert year.ours == "1999"
    assert year.canonical == "1998"


def test_compare_flags_wrong_author():
    from latextify.citations.crossref import candidate_from_item

    # A substantially different surname (not a legitimate variant) must flag.
    entry = _entry(authors=(Name(family="Lovelace"), Name(family="Wozniak")))
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    authors = next(c for c in checks if c.field == "authors")
    assert authors.ok is False


def test_compare_tolerates_accent_transliteration():
    from latextify.citations.crossref import candidate_from_item

    # Manuscript ASCII-transliterates an accented Crossref name -- not an error.
    work = _work(author=[{"family": "Néel"}, {"family": "González"}])
    entry = _entry(authors=(Name(family="Neel"), Name(family="Gonzalez")))
    checks = compare_entry_to_candidate(entry, candidate_from_item(work))
    authors = next(c for c in checks if c.field == "authors")
    assert authors.ok is True


def test_compare_flags_dropped_author():
    from latextify.citations.crossref import candidate_from_item

    entry = _entry(authors=(Name(family="Lovelace"),))  # Babbage dropped
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    authors = next(c for c in checks if c.field == "authors")
    assert authors.ok is False


def test_compare_tolerates_journal_abbreviation():
    from latextify.citations.crossref import candidate_from_item

    # Manuscript cites the abbreviated journal; Crossref's short-container-title
    # must let it validate.
    entry = _entry(container_title="J. Widgets")
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    journal = next(c for c in checks if c.field == "journal")
    assert journal.ok is True


def test_compare_flags_wrong_journal():
    from latextify.citations.crossref import candidate_from_item

    entry = _entry(container_title="Reviews of Modern Llamas")
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    journal = next(c for c in checks if c.field == "journal")
    assert journal.ok is False


def test_compare_tolerates_first_page_only():
    from latextify.citations.crossref import candidate_from_item

    # Author cites only the first page; Crossref has the full range.
    entry = _entry(pages="45")
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    pages = next(c for c in checks if c.field == "pages")
    assert pages.ok is True


def test_compare_tolerates_en_dash_pages():
    from latextify.citations.crossref import candidate_from_item

    entry = _entry(pages="45–67")  # en dash
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    pages = next(c for c in checks if c.field == "pages")
    assert pages.ok is True


def test_compare_skips_fields_missing_on_our_side():
    from latextify.citations.crossref import candidate_from_item

    entry = _entry(volume=None, issue=None, pages=None)
    checks = compare_entry_to_candidate(entry, candidate_from_item(_work()))
    fields = {c.field for c in checks}
    assert "volume" not in fields and "issue" not in fields and "pages" not in fields
    assert "title" in fields  # still compares what IS present


# --------------------------------------------------------------------------- #
# validate_entry -- DOI path
# --------------------------------------------------------------------------- #


def test_validate_verified_when_doi_resolves_and_matches():
    with _client(by_doi={"10.1000/widgets.1998": _work()}) as client:
        record = validate_entry(_entry(), client)
    assert record.status == "verified"
    assert record.flagged is False
    assert record.doi == "10.1000/widgets.1998"


def test_validate_mismatch_when_field_differs():
    with _client(by_doi={"10.1000/widgets.1998": _work(volume="99")}) as client:
        record = validate_entry(_entry(), client)
    assert record.status == "mismatch"
    assert record.flagged is True
    problems = {c.field for c in record.problems}
    assert "volume" in problems


def test_validate_dead_doi_on_404():
    # DOI present in the entry but not registered in Crossref -> dead_doi.
    with _client(by_doi={}) as client:
        record = validate_entry(_entry(doi="10.9999/fabricated"), client)
    assert record.status == "dead_doi"
    assert record.flagged is True


def test_validate_unchecked_when_crossref_unavailable():
    with _client(by_doi={"10.1000/widgets.1998": _work()}, doi_status=503) as client:
        record = validate_entry(_entry(), client)
    # A 503 is a Crossref outage, NOT a bad reference.
    assert record.status == "unchecked"
    assert record.flagged is False


# --------------------------------------------------------------------------- #
# validate_entry -- no-DOI path
# --------------------------------------------------------------------------- #


def test_validate_suggests_doi_for_doiless_reference():
    entry = _entry(doi=None)
    with _client(by_query={"widgets": [_work()]}) as client:
        record = validate_entry(entry, client)
    assert record.status == "doi_suggested"
    assert record.suggested_doi == "10.1000/widgets.1998"
    assert record.flagged is True


def test_validate_unverifiable_when_no_match():
    entry = _entry(doi=None, title="An Utterly Untraceable Manuscript", container_title=None,
                   authors=(Name(family="Nobody"),))
    with _client(by_query={}) as client:
        record = validate_entry(entry, client)
    assert record.status == "unverifiable"
    assert record.flagged is True


# --------------------------------------------------------------------------- #
# validate_references -- whole-list behavior
# --------------------------------------------------------------------------- #


def test_validate_references_reports_per_entry():
    entries = [_entry(key="a"), _entry(key="b", doi="10.9999/nope")]
    with _client(by_doi={"10.1000/widgets.1998": _work()}) as client:
        report = validate_references(entries, client)
    assert report.total == 2
    assert report.count("verified") == 1
    assert report.count("dead_doi") == 1
    assert report.flagged_count == 1
    assert report.any_checked is True


def test_offline_after_first_failure_marks_rest_unchecked():
    # Once Crossref is unreachable, every remaining reference is unchecked
    # without a doomed request -- no wall of spurious flags, no dozens of timeouts.
    entries = [_entry(key="a"), _entry(key="b"), _entry(key="c")]
    with _client(by_doi={"10.1000/widgets.1998": _work()}, doi_status=500) as client:
        report = validate_references(entries, client)
    assert report.count("unchecked") == 3
    assert report.any_checked is False
    assert report.flagged_count == 0


def test_no_doi_reference_unchecked_on_outage():
    # A no-DOI reference during a Crossref outage is 'unchecked', not mislabeled
    # 'unverifiable' (tech-debt finding 1: the no-DOI path swallowed the outage).
    with _client(query_status=503) as client:
        record = validate_entry(_entry(doi=None), client)
    assert record.status == "unchecked"
    assert record.flagged is False


def test_offline_short_circuit_engages_from_no_doi_outage():
    # The whole-list offline short-circuit must trip even when the first failing
    # reference has no DOI -- its query path is what detects the outage.
    entries = [_entry(key="a", doi=None), _entry(key="b", doi=None)]
    with _client(query_status=503) as client:
        report = validate_references(entries, client)
    assert report.count("unchecked") == 2
    assert report.any_checked is False
