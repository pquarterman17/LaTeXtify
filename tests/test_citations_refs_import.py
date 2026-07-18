"""Extension-based dispatch across every bibliography input format."""

from __future__ import annotations

import json

import pytest

from latextify.citations.refs_import import parse_references_file

_BIB = "@article{k, title={A Title}, author={Doe, Jane}, year={2020}}\n"
_CSL_JSON = json.dumps(
    [{"id": "k", "type": "article-journal", "title": "A Title", "issued": {"date-parts": [[2020]]}}]
)
_ENDNOTE_XML = (
    b"<xml><records><record><rec-number>k</rec-number>"
    b"<titles><title>A Title</title></titles>"
    b"<dates><year>2020</year></dates>"
    b"</record></records></xml>"
)
_NBIB = "PMID- k\nTI  - A Title\nDP  - 2020\n"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("lib.bib", _BIB),
        ("lib.ris", _BIB),  # no dedicated RIS grammar yet: routed to the BibTeX parser
        ("lib.json", _CSL_JSON),
        ("lib.xml", _ENDNOTE_XML),
        ("lib.nbib", _NBIB),
        ("LIB.BIB", _BIB),  # extension matching is case-insensitive
    ],
)
def test_dispatches_by_extension_and_parses(tmp_path, filename, content):
    path = tmp_path / filename
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")

    entries = parse_references_file(path)

    assert len(entries) == 1
    assert entries[0].title == "A Title"


def test_unrecognized_extension_raises_value_error(tmp_path):
    path = tmp_path / "lib.docx"
    path.write_text("not a references file", encoding="utf-8")

    with pytest.raises(ValueError, match="unrecognized references file type"):
        parse_references_file(path)


def test_corrupt_json_raises_value_error_naming_the_path(tmp_path):
    path = tmp_path / "lib.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match=r"lib\.json"):
        parse_references_file(path)


def test_accepts_a_string_path_too(tmp_path):
    path = tmp_path / "lib.bib"
    path.write_text(_BIB, encoding="utf-8")

    entries = parse_references_file(str(path))

    assert entries[0].title == "A Title"
