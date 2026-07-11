"""Key generation edge cases + BibTeX emission."""

from __future__ import annotations

from latextify.citations.bib import (
    ascii_fold,
    assign_keys,
    entries_to_bib,
    escape_latex,
    make_base_key,
    protect_title,
    to_bibtex,
)
from latextify.model.refs import Name, RefEntry


def _entry(**kw) -> RefEntry:
    base = dict(key="", entry_type="article", csl_type="article-journal")
    base.update(kw)
    return RefEntry(**base)


# --- key generation ----------------------------------------------------------


def test_base_key_standard():
    e = _entry(
        authors=(Name(family="Smith", given="Alice"),),
        year="2020",
        title="Quantum transport phenomena",
    )
    assert make_base_key(e) == "smith2020quantum"


def test_key_ascii_folds_unicode_author_and_title():
    e = _entry(
        authors=(Name(family="Nyström", given="Erik"),),
        year="2019",
        title="Éclat of superconductivity",
    )
    assert make_base_key(e) == "nystrom2019eclat"


def test_key_skips_leading_stopword_in_title():
    e = _entry(
        authors=(Name(family="Doe"),),
        year="2001",
        title="On the theory of everything",
    )
    assert make_base_key(e) == "doe2001theory"


def test_missing_year_uses_nd():
    e = _entry(authors=(Name(family="Doe"),), year=None, title="Foo bar")
    assert make_base_key(e) == "doendfoo"


def test_missing_author_falls_back_to_title_word():
    e = _entry(authors=(), year="2010", title="Neutron scattering primer")
    assert make_base_key(e) == "neutron2010"


def test_missing_author_and_title_uses_anon():
    e = _entry(authors=(), year="2010", title=None)
    assert make_base_key(e) == "anon2010"


def test_literal_author_used_in_key():
    e = _entry(authors=(Name(literal="CERN Collaboration"),), year="2012", title="Higgs search")
    assert make_base_key(e) == "cern2012higgs"


def test_collisions_get_abc_suffixes():
    common = dict(
        authors=(Name(family="Smith"),), year="2020", title="Quantum foo bar"
    )
    a = _entry(doi="10.1/a", **common)
    b = _entry(doi="10.1/b", **common)
    c = _entry(doi="10.1/c", **common)
    keyed = assign_keys([a, b, c])
    assert [e.key for e in keyed] == ["smith2020quantuma", "smith2020quantumb", "smith2020quantumc"]


def test_non_colliding_key_has_no_suffix():
    a = _entry(authors=(Name(family="Smith"),), year="2020", title="Alpha study")
    b = _entry(authors=(Name(family="Jones"),), year="2020", title="Beta study")
    keyed = assign_keys([a, b])
    assert [e.key for e in keyed] == ["smith2020alpha", "jones2020beta"]


def test_ascii_fold_special_letters():
    assert ascii_fold("Łukasiewicz") == "Lukasiewicz"
    assert ascii_fold("Straße") == "Strasse"
    assert ascii_fold("Ångström") == "Angstrom"


# --- LaTeX escaping + title protection ---------------------------------------


def test_escape_latex_specials():
    assert escape_latex("a & b") == r"a \& b"
    assert escape_latex("50%") == r"50\%"
    assert escape_latex("Fe_2O_3") == r"Fe\_2O\_3"
    assert escape_latex("#tag") == r"\#tag"


def test_protect_title_braces_internal_capitals():
    out = protect_title("Growth of GaAs and CO2 uptake")
    assert "{GaAs}" in out
    assert "{CO2}" in out
    # Ordinary title-cased words (single leading capital) are not braced.
    assert "{Growth}" not in out


def test_protect_title_escapes_specials_inside():
    out = protect_title("Studies of Fe_2 & pH")
    assert r"Fe\_2" in out or r"{Fe\_2}" in out
    assert r"\&" in out


# --- full record emission ----------------------------------------------------


def test_to_bibtex_article():
    e = RefEntry(
        key="smith2020quantum",
        entry_type="article",
        csl_type="article-journal",
        title="Quantum transport in GaAs",
        authors=(Name(family="Smith", given="Alice"), Name(family="Zhang", given="Wei")),
        year="2020",
        container_title="Physical Review B",
        volume="101",
        issue="4",
        pages="045123",
        doi="10.1103/PhysRevB.101.045123",
    )
    text = to_bibtex(e)
    assert text.startswith("@article{smith2020quantum,")
    assert "author = {Smith, Alice and Zhang, Wei}" in text
    assert "journal = {Physical Review B}" in text
    assert "number = {4}" in text
    assert "doi = {10.1103/PhysRevB.101.045123}" in text
    assert "{GaAs}" in text


def test_to_bibtex_pages_endash():
    e = _entry(key="k", authors=(Name(family="Doe"),), year="2019", pages="12-19")
    assert "pages = {12--19}" in to_bibtex(e)


def test_to_bibtex_inproceedings_uses_booktitle():
    e = RefEntry(
        key="k",
        entry_type="inproceedings",
        csl_type="paper-conference",
        title="A talk",
        container_title="Proc. of Things",
    )
    assert "booktitle = {Proc. of Things}" in to_bibtex(e)


def test_to_bibtex_literal_author_braced():
    e = _entry(key="k", authors=(Name(literal="CERN Collaboration"),), title="X")
    assert "author = {{CERN Collaboration}}" in to_bibtex(e)


def test_entries_to_bib_joins_records():
    a = _entry(key="a", authors=(Name(family="Doe"),), title="A")
    b = _entry(key="b", authors=(Name(family="Roe"),), title="B")
    bib = entries_to_bib([a, b])
    assert "@article{a," in bib
    assert "@article{b," in bib
