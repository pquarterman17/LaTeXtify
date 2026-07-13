"""Applying reviewed reference corrections back onto the bibliography."""

from __future__ import annotations

from latextify.citations.corrections import (
    apply_corrections,
    authors_from_text,
    authors_to_text,
    entry_from_dict,
    entry_to_dict,
)
from latextify.model.refs import Name, RefEntry
from latextify.model.validate import (
    CorrectionDecision,
    FieldCheck,
    ValidationRecord,
    ValidationReport,
)


def _entry(**over):
    fields = {
        "key": "smith2019",
        "entry_type": "article",
        "title": "A Study of Widgets",
        "authors": (Name(family="Smith", given="Jane"), Name(family="Jones", given="Bob")),
        "year": "2019",
        "container_title": "Journal of Widgets",
        "volume": "12",
        "issue": "3",
        "pages": "45",
        "doi": "10.1/abc",
        "source": "crossref",
    }
    fields.update(over)
    return RefEntry(**fields)


# --------------------------------------------------------------------------- #
# author (de)serialization
# --------------------------------------------------------------------------- #


def test_authors_round_trip_personal_names():
    names = (Name(family="Néel", given="Louis"), Name(family="Smith", given="J."))
    text = authors_to_text(names)
    assert text == "Néel, Louis; Smith, J."
    back = authors_from_text(text)
    assert back[0].family == "Néel" and back[0].given == "Louis"
    assert back[1].family == "Smith" and back[1].given == "J."


def test_authors_family_only_and_literal():
    assert authors_to_text((Name(family="Curie"),)) == "Curie"
    assert authors_to_text((Name(literal="CERN Collaboration"),)) == "CERN Collaboration"
    parsed = authors_from_text("Curie; Wozniak, Steve")
    assert parsed[0].family == "Curie" and parsed[0].given == ""
    assert parsed[1].family == "Wozniak" and parsed[1].given == "Steve"


def test_authors_from_text_ignores_blank_segments():
    assert authors_from_text("  ; Smith, J. ;;") == (Name(family="Smith", given="J."),)


# --------------------------------------------------------------------------- #
# entry <-> dict
# --------------------------------------------------------------------------- #


def test_entry_to_dict_flattens_fields():
    d = entry_to_dict(_entry())
    assert d["title"] == "A Study of Widgets"
    assert d["authors"] == "Smith, Jane; Jones, Bob"
    assert d["journal"] == "Journal of Widgets"
    assert d["doi"] == "10.1/abc"


def test_entry_from_dict_preserves_identity_and_blanks_to_none():
    base = _entry()
    edited = entry_from_dict(
        {"title": "New Title", "authors": "Smith, Jane", "year": "2020",
         "journal": "", "volume": "", "issue": "", "pages": "", "doi": "10.2/x"},
        base=base,
    )
    assert edited.key == "smith2019"  # identity preserved
    assert edited.entry_type == "article"
    assert edited.source == "crossref"
    assert edited.title == "New Title"
    assert edited.year == "2020"
    assert edited.container_title is None  # blank -> None
    assert edited.volume is None
    assert edited.doi == "10.2/x"
    assert edited.authors == (Name(family="Smith", given="Jane"),)


# --------------------------------------------------------------------------- #
# apply_corrections
# --------------------------------------------------------------------------- #


def _report(*records):
    return ValidationReport(records=tuple(records))


def test_deny_and_no_decision_leave_entry_unchanged():
    entry = _entry()
    report = _report(
        ValidationRecord(
            key="smith2019", status="mismatch", doi="10.1/abc",
            checks=(FieldCheck(field="year", ours="2019", canonical="2020", ok=False),),
            canonical_entry=_entry(year="2020"),
        )
    )
    # explicit deny
    out = apply_corrections([entry], report, [CorrectionDecision(key="smith2019", action="deny")])
    assert out[0] == entry
    # no decision at all
    out2 = apply_corrections([entry], report, [])
    assert out2[0] == entry


def test_approve_adopts_only_flagged_canonical_fields():
    entry = _entry(year="2019", volume="12")
    # Crossref says year 2020, volume 99 -- but only YEAR was flagged.
    canonical = _entry(year="2020", volume="99")
    report = _report(
        ValidationRecord(
            key="smith2019", status="mismatch", doi="10.1/abc",
            checks=(FieldCheck(field="year", ours="2019", canonical="2020", ok=False),),
            canonical_entry=canonical,
        )
    )
    out = apply_corrections(
        [entry], report, [CorrectionDecision(key="smith2019", action="approve")]
    )
    assert out[0].year == "2020"  # flagged field adopted
    assert out[0].volume == "12"  # unflagged field untouched


def test_approve_restores_full_author_tuple_not_display_string():
    # The FieldCheck display truncates authors; approving must restore the FULL
    # structured author list from canonical_entry.
    entry = _entry(authors=(Name(family="Smith"),))
    full = (Name(family="Smith", given="Jane"), Name(family="Jones"), Name(family="Lee"))
    canonical = _entry(authors=full)
    report = _report(
        ValidationRecord(
            key="smith2019", status="mismatch", doi="10.1/abc",
            checks=(FieldCheck(field="authors", ours="Smith", canonical="Smith, Jones, +1",
                               ok=False),),
            canonical_entry=canonical,
        )
    )
    out = apply_corrections(
        [entry], report, [CorrectionDecision(key="smith2019", action="approve")]
    )
    assert out[0].authors == full


def test_approve_doi_suggested_adds_doi():
    entry = _entry(doi=None)
    canonical = _entry(doi="10.5/suggested")
    report = _report(
        ValidationRecord(
            key="smith2019", status="doi_suggested", suggested_doi="10.5/suggested",
            canonical_entry=canonical,
        )
    )
    out = apply_corrections(
        [entry], report, [CorrectionDecision(key="smith2019", action="approve")]
    )
    assert out[0].doi == "10.5/suggested"


def test_edit_replaces_entry_but_keeps_key():
    entry = _entry()
    replacement = _entry(key="WRONGKEY", title="Corrected Title", year="2021")
    report = _report(
        ValidationRecord(key="smith2019", status="dead_doi", doi="10.1/dead")
    )
    out = apply_corrections(
        [entry], report,
        [CorrectionDecision(key="smith2019", action="edit", edited_entry=replacement)],
    )
    assert out[0].title == "Corrected Title"
    assert out[0].year == "2021"
    assert out[0].key == "smith2019"  # original key preserved for \cite validity


def test_apply_preserves_order_and_count_and_ignores_unknown_keys():
    a, b, c = _entry(key="a"), _entry(key="b"), _entry(key="c")
    report = _report(ValidationRecord(key="b", status="dead_doi"))
    out = apply_corrections(
        [a, b, c], report,
        [
            CorrectionDecision(key="b", action="edit", edited_entry=_entry(title="B!")),
            CorrectionDecision(key="ghost", action="approve"),  # no such entry
        ],
    )
    assert [e.key for e in out] == ["a", "b", "c"]
    assert out[1].title == "B!"
