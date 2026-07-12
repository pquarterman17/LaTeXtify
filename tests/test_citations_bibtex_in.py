"""Parse a user-supplied ``.bib`` into RefEntry records (the reader half)."""

from __future__ import annotations

from latextify.citations.bibtex_in import parse_bibtex

# --- basic article -----------------------------------------------------------


def test_parses_article_fields():
    text = r"""
    @article{cornelissen2015,
      title   = {Long-distance transport of magnon spin information},
      author  = {Cornelissen, L. J. and van Wees, B. J.},
      journal = {Nature Physics},
      volume  = {11},
      number  = {12},
      pages   = {1022--1026},
      year    = {2015},
      doi     = {10.1038/nphys3465},
    }
    """
    (e,) = parse_bibtex(text)
    assert e.key == "cornelissen2015"
    assert e.entry_type == "article"
    assert e.title == "Long-distance transport of magnon spin information"
    assert e.container_title == "Nature Physics"
    assert e.volume == "11"
    assert e.issue == "12"  # `number` maps to issue
    assert e.pages == "1022--1026"
    assert e.year == "2015"
    assert e.doi == "10.1038/nphys3465"
    assert e.source == "bibfile"
    assert e.raw_id == "cornelissen2015"


def test_author_family_given_split():
    text = r"""
    @article{k, title={T}, year={2020},
      author = {Cornelissen, L. J. and Liu, J. and Ben Youssef, J.}}
    """
    (e,) = parse_bibtex(text)
    assert [(n.family, n.given) for n in e.authors] == [
        ("Cornelissen", "L. J."),
        ("Liu", "J."),
        ("Ben Youssef", "J."),
    ]


def test_author_no_comma_uses_last_word_as_family():
    text = r"@article{k, title={T}, year={2020}, author = {Charles Kittel}}"
    (e,) = parse_bibtex(text)
    assert [(n.family, n.given) for n in e.authors] == [("Kittel", "Charles")]


def test_author_others_becomes_literal():
    text = r"@article{k, title={T}, year={2020}, author = {Smith, A. and others}}"
    (e,) = parse_bibtex(text)
    assert e.authors[-1].literal == "others"


# --- delimiters + value cleaning ---------------------------------------------


def test_quoted_values_are_unwrapped():
    text = r'@article{k, title = "A quoted title", year = "2019"}'
    (e,) = parse_bibtex(text)
    assert e.title == "A quoted title"
    assert e.year == "2019"


def test_case_protection_braces_stripped_from_title():
    text = r"@article{k, title = {Growth of {GaAs} and {CO2}}, year={2020}}"
    (e,) = parse_bibtex(text)
    assert e.title == "Growth of GaAs and CO2"


def test_paren_delimited_entry():
    text = r"@article(k, title = {Paren entry}, year = {2001})"
    (e,) = parse_bibtex(text)
    assert e.key == "k"
    assert e.title == "Paren entry"


def test_doi_url_prefix_normalized():
    text = r"@article{k, title={T}, year={2020}, doi = {https://doi.org/10.1/xyz}}"
    (e,) = parse_bibtex(text)
    assert e.doi == "10.1/xyz"


def test_year_extracted_from_date_field():
    text = r"@article{k, title={T}, date = {2018-05-14}}"
    (e,) = parse_bibtex(text)
    assert e.year == "2018"


# --- @string macros ----------------------------------------------------------


def test_string_macro_resolved_in_journal():
    text = r"""
    @string{np = {Nature Physics}}
    @article{k, title={T}, year={2020}, journal = np}
    """
    (e,) = parse_bibtex(text)
    assert e.container_title == "Nature Physics"


def test_string_macro_defined_after_use_still_resolves():
    # BibTeX resolves macros globally; a two-pass parser must too.
    text = r"""
    @article{k, title={T}, year={2020}, journal = prl}
    @string{prl = {Physical Review Letters}}
    """
    (e,) = parse_bibtex(text)
    assert e.container_title == "Physical Review Letters"


def test_bare_number_left_as_is_when_no_macro():
    text = r"@article{k, title={T}, year={2020}, volume = 12}"
    (e,) = parse_bibtex(text)
    assert e.volume == "12"


# --- skipping + robustness ---------------------------------------------------


def test_string_preamble_comment_are_skipped():
    text = r"""
    @preamble{ "\newcommand{\noop}[1]{}" }
    @comment{ jabref-meta: databaseType:bibtex; }
    @string{x = {Y}}
    @article{k, title={Kept}, year={2020}}
    """
    entries = parse_bibtex(text)
    assert [e.key for e in entries] == ["k"]


def test_container_field_synonyms_first_wins():
    # booktitle should populate container_title when journal is absent.
    text = r"@inproceedings{k, title={Talk}, year={2020}, booktitle = {Proc. of Things}}"
    (e,) = parse_bibtex(text)
    assert e.container_title == "Proc. of Things"


def test_book_publisher_captured():
    text = r"@book{k, title={Solid State}, author={Kittel, C.}, publisher={Wiley}, year={2005}}"
    (e,) = parse_bibtex(text)
    assert e.entry_type == "book"
    assert e.publisher == "Wiley"


def test_multiple_entries_order_preserved():
    text = r"""
    @article{first, title={A}, year={2001}}
    @book{second, title={B}, year={2002}}
    @misc{third, title={C}, year={2003}}
    """
    assert [e.key for e in parse_bibtex(text)] == ["first", "second", "third"]


def test_malformed_entry_does_not_discard_neighbours():
    # An unterminated entry (no closing brace) is skipped; the valid one survives.
    text = r"""
    @article{broken, title = {No closing brace
    @article{good, title = {Fine}, year = {2020}}
    """
    keys = [e.key for e in parse_bibtex(text)]
    assert "good" in keys


def test_entry_without_key_is_dropped():
    text = r"@article{, title={No key}, year={2020}}"
    assert parse_bibtex(text) == []


def test_empty_input_returns_empty_list():
    assert parse_bibtex("") == []
    assert parse_bibtex("   \n\n  ") == []


def test_commas_inside_braces_not_treated_as_field_separators():
    text = r"@article{k, title = {A, B, and C}, author = {Doe, Jane}, year={2020}}"
    (e,) = parse_bibtex(text)
    assert e.title == "A, B, and C"
    assert [(n.family, n.given) for n in e.authors] == [("Doe", "Jane")]
