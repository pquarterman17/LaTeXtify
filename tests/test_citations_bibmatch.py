"""Offline .bib matching: match typed references against a local .bib first.

These tests never touch the network: a matched reference must resolve from the
parsed ``.bib`` alone (proved with a Crossref client that raises if queried),
and an unmatched reference must fall through to the injected client.
"""

from __future__ import annotations

import pytest

from latextify.citations.bibmatch import best_bib_entry, first_author_surname, score_bib_entry
from latextify.citations.bibtex_in import parse_bibtex
from latextify.citations.reconcile import ReferenceItem, reconcile_references
from latextify.model.refs import Name, RefEntry

SAMPLE_BIB = r"""
@article{cornelissen2015,
  title   = {Long-distance transport of magnon spin information in a magnetic
             insulator at room temperature},
  author  = {Cornelissen, L. J. and Liu, J. and Duine, R. A. and van Wees, B. J.},
  journal = {Nature Physics},
  volume  = {11},
  pages   = {1022--1026},
  year    = {2015},
  doi     = {10.1038/nphys3465},
}

@article{kikkawa2013,
  title   = {Longitudinal spin Seebeck effect free from the proximity Nernst effect},
  author  = {Kikkawa, T. and Uchida, K. and Saitoh, E.},
  journal = {Physical Review Letters},
  year    = {2013},
  doi     = {10.1103/PhysRevLett.110.067207},
}
"""

_CORNELISSEN = (
    "L. J. Cornelissen, J. Liu, R. A. Duine, B. J. van Wees, Long-distance transport "
    "of magnon spin information in a magnetic insulator at room temperature, "
    "Nature Physics 11, 1022 (2015)."
)
_KIKKAWA = (
    "T. Kikkawa, K. Uchida, E. Saitoh, Longitudinal spin Seebeck effect free from "
    "the proximity Nernst effect, Physical Review Letters (2013)."
)
_UNRELATED = "Nobody, An unrelated paper on algae photosynthesis, Botany Today (1998)."


class _ExplodingClient:
    """Fails the test if queried -- proves a matched reference stayed offline."""

    def query_bibliographic(self, text, rows=3):
        raise AssertionError(f"Crossref queried for {text!r}; the .bib should have matched")


class _RecordingClient:
    """Records queries and returns no candidates (forces the raw fallback)."""

    def __init__(self):
        self.queries: list[str] = []

    def query_bibliographic(self, text, rows=3):
        self.queries.append(text)
        return []


# --- surname extraction ------------------------------------------------------


def test_first_author_surname_prefers_family():
    e = RefEntry(key="k", entry_type="article", authors=(Name(family="Cornelissen", given="L."),))
    assert first_author_surname(e) == "Cornelissen"


def test_first_author_surname_falls_back_to_literal():
    e = RefEntry(key="k", entry_type="article", authors=(Name(literal="CERN Collaboration"),))
    assert first_author_surname(e) == "CERN Collaboration"


def test_first_author_surname_none_when_no_authors():
    assert first_author_surname(RefEntry(key="k", entry_type="article")) is None


# --- scoring -----------------------------------------------------------------


def test_score_high_for_matching_reference():
    cornelissen = parse_bibtex(SAMPLE_BIB)[0]
    assert score_bib_entry(_CORNELISSEN, cornelissen) >= 0.72


def test_best_bib_entry_picks_matching_title():
    best, score = best_bib_entry(_CORNELISSEN, parse_bibtex(SAMPLE_BIB))
    assert best is not None
    assert best.doi == "10.1038/nphys3465"
    assert score >= 0.72


def test_best_bib_entry_low_score_for_unrelated():
    _best, score = best_bib_entry(_UNRELATED, parse_bibtex(SAMPLE_BIB))
    assert score < 0.72


def test_best_bib_entry_empty_list():
    best, score = best_bib_entry(_CORNELISSEN, [])
    assert best is None
    assert score == 0.0


# --- reconcile integration ---------------------------------------------------


def test_reconcile_matches_bib_without_network():
    entries = parse_bibtex(SAMPLE_BIB)
    refs = [
        ReferenceItem(text=_CORNELISSEN, number=1),
        ReferenceItem(text=_KIKKAWA, number=2),
    ]
    outcome = reconcile_references(refs, _ExplodingClient(), bib_entries=entries)

    assert len(outcome.entries) == 2
    assert all(r.source == "bibfile" and r.matched and not r.verify for r in outcome.records)
    assert outcome.records[0].doi == "10.1038/nphys3465"
    # Keys are freshly assigned across the list, so they must be collision-free.
    assert len({e.key for e in outcome.entries}) == 2


def test_reconcile_falls_through_to_crossref_when_bib_misses():
    client = _RecordingClient()
    refs = [ReferenceItem(text=_UNRELATED, number=1)]
    outcome = reconcile_references(refs, client, bib_entries=parse_bibtex(SAMPLE_BIB))

    assert client.queries == [_UNRELATED]  # the .bib missed -> Crossref consulted
    assert outcome.records[0].source == "raw"
    assert outcome.records[0].verify


def test_reconcile_mixed_bib_hit_and_miss():
    client = _RecordingClient()
    refs = [
        ReferenceItem(text=_CORNELISSEN, number=1),  # in the .bib
        ReferenceItem(text=_UNRELATED, number=2),  # not in the .bib
    ]
    outcome = reconcile_references(refs, client, bib_entries=parse_bibtex(SAMPLE_BIB))

    assert outcome.records[0].source == "bibfile"
    assert outcome.records[1].source == "raw"
    # Only the unmatched reference reached the network.
    assert client.queries == [_UNRELATED]


def test_reconcile_without_bib_uses_crossref_as_before():
    client = _RecordingClient()
    outcome = reconcile_references([ReferenceItem(text=_CORNELISSEN, number=1)], client)
    assert client.queries == [_CORNELISSEN]
    assert outcome.records[0].source == "raw"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
