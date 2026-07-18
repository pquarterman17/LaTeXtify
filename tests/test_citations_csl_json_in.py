"""Parse a user-supplied CSL-JSON references export into RefEntry records."""

from __future__ import annotations

import json

import pytest

from latextify.citations.csl_json_in import parse_csl_json

ARRAY_TEXT = json.dumps(
    [
        {
            "id": "placeholder2020strain",
            "type": "article-journal",
            "title": "Strain-tunable magnon transport in a placeholder oxide",
            "author": [
                {"family": "Placeholder", "given": "Pat"},
                {"family": "Example", "given": "Evan"},
            ],
            "issued": {"date-parts": [[2020, 6]]},
            "container-title": "Journal of Placeholder Physics",
            "volume": "12",
            "issue": "3",
            "page": "100-110",
            "DOI": "10.1000/placeholder.2020",
        },
        {
            "id": "sample1999book",
            "type": "book",
            "title": "Fundamentals of Placeholder Materials",
            "author": [{"family": "Sample", "given": "Sam"}],
            "issued": {"date-parts": [[1999]]},
            "publisher": "Fictitious Press",
        },
    ]
)


def test_parses_array_of_items_all_fields_mapped():
    entries = parse_csl_json(ARRAY_TEXT)
    assert len(entries) == 2

    article = entries[0]
    assert article.key == "placeholder2020strain"
    assert article.raw_id == "placeholder2020strain"
    assert article.entry_type == "article"
    assert article.title == "Strain-tunable magnon transport in a placeholder oxide"
    assert [(a.family, a.given) for a in article.authors] == [
        ("Placeholder", "Pat"),
        ("Example", "Evan"),
    ]
    assert article.year == "2020"
    assert article.container_title == "Journal of Placeholder Physics"
    assert article.volume == "12"
    assert article.issue == "3"
    assert article.pages == "100-110"
    assert article.doi == "10.1000/placeholder.2020"
    assert article.source == "csl-json"

    book = entries[1]
    assert book.key == "sample1999book"
    assert book.entry_type == "book"
    assert book.year == "1999"


def test_object_with_items_array_is_also_accepted():
    wrapped = json.dumps({"items": json.loads(ARRAY_TEXT)})
    entries = parse_csl_json(wrapped)
    assert [e.key for e in entries] == ["placeholder2020strain", "sample1999book"]


def test_missing_fields_tolerated():
    text = json.dumps([{"id": "bare2001", "type": "article-journal", "title": "Bare Title"}])
    (entry,) = parse_csl_json(text)
    assert entry.title == "Bare Title"
    assert entry.authors == ()
    assert entry.year is None
    assert entry.container_title is None
    assert entry.doi is None


def test_item_without_id_stays_keyless():
    text = json.dumps([{"type": "article-journal", "title": "No id here"}])
    (entry,) = parse_csl_json(text)
    assert entry.key == ""
    assert entry.raw_id is None


def test_non_dict_items_in_array_are_skipped():
    text = json.dumps([{"id": "a1", "type": "book", "title": "Kept"}, "not an item", 42])
    entries = parse_csl_json(text)
    assert [e.title for e in entries] == ["Kept"]


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_csl_json("{not json at all")


def test_json_that_is_not_csl_shaped_raises_value_error():
    with pytest.raises(ValueError, match="CSL-JSON"):
        parse_csl_json(json.dumps({"foo": "bar"}))
    with pytest.raises(ValueError, match="CSL-JSON"):
        parse_csl_json(json.dumps("just a string"))


def test_empty_array_returns_empty_list():
    assert parse_csl_json("[]") == []
