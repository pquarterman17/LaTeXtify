"""Parse a user-supplied PubMed MEDLINE .nbib export into RefEntry records."""

from __future__ import annotations

import pytest

from latextify.citations.nbib_in import parse_nbib

TWO_RECORDS = """\
PMID- 12345678
OWN - NLM
STAT- MEDLINE
TI  - Strain-tunable magnon transport in a placeholder
      oxide
AU  - Placeholder P
AU  - Example E
DP  - 2020 Jun 15
JT  - Journal of Placeholder Physics
TA  - J Placeholder Phys
VI  - 12
IP  - 3
PG  - 100-110
AID - 10.1000/pii.reference [pii]
LID - 10.1000/placeholder.2020 [doi]

PMID- 87654321
OWN - NLM
STAT- MEDLINE
TI  - Fundamentals of Placeholder Materials
AU  - Sample S
DP  - 1999
JT  - Book Abstracts
"""


def test_parses_two_records_all_fields_mapped():
    entries = parse_nbib(TWO_RECORDS)
    assert len(entries) == 2

    first = entries[0]
    assert first.key == "12345678"
    assert first.raw_id == "12345678"
    assert first.entry_type == "article"
    assert first.title == "Strain-tunable magnon transport in a placeholder oxide"
    assert [(a.family, a.given) for a in first.authors] == [
        ("Placeholder", "P"),
        ("Example", "E"),
    ]
    assert first.year == "2020"
    assert first.container_title == "Journal of Placeholder Physics"
    assert first.volume == "12"
    assert first.issue == "3"
    assert first.pages == "100-110"
    assert first.doi == "10.1000/placeholder.2020"
    assert first.source == "nbib"

    second = entries[1]
    assert second.key == "87654321"
    assert second.title == "Fundamentals of Placeholder Materials"
    assert second.year == "1999"


def test_journal_falls_back_to_abbreviated_title_when_full_title_absent():
    text = "PMID- 1\nTI  - T\nTA  - Abbrev Only\n"
    (entry,) = parse_nbib(text)
    assert entry.container_title == "Abbrev Only"


def test_missing_fields_tolerated():
    text = "PMID- 1\nTI  - Bare Record\n"
    (entry,) = parse_nbib(text)
    assert entry.title == "Bare Record"
    assert entry.authors == ()
    assert entry.year is None
    assert entry.container_title is None
    assert entry.doi is None
    assert entry.volume is None


def test_record_without_pmid_is_dropped():
    text = "TI  - No id here\nAU  - Nobody N\n"
    assert parse_nbib(text) == []


def test_records_without_blank_line_separator_still_split():
    text = "PMID- 1\nTI  - First\nPMID- 2\nTI  - Second\n"
    entries = parse_nbib(text)
    assert [e.title for e in entries] == ["First", "Second"]
    assert [e.key for e in entries] == ["1", "2"]


def test_single_word_author_becomes_literal_name():
    text = "PMID- 1\nTI  - T\nAU  - CorporateAuthor\n"
    (entry,) = parse_nbib(text)
    assert entry.authors[0].literal == "CorporateAuthor"


def test_empty_input_returns_empty_list():
    assert parse_nbib("") == []
    assert parse_nbib("   \n\n  ") == []


def test_corrupt_input_raises_value_error():
    with pytest.raises(ValueError, match="not a valid PubMed .nbib export"):
        parse_nbib("this is just some prose, not a MEDLINE export at all.")
