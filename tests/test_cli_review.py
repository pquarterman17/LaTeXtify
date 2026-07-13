"""Interactive console review loop (latextify.cli_review)."""

from __future__ import annotations

from latextify.cli_review import describe_record, review_corrections
from latextify.model.refs import Name, RefEntry
from latextify.model.validate import FieldCheck, ValidationRecord, ValidationReport


def _entry(**over):
    fields = {
        "key": "smith2019",
        "entry_type": "article",
        "title": "A Study of Widgets",
        "authors": (Name(family="Smith", given="Jane"),),
        "year": "2019",
        "container_title": "Journal of Widgets",
        "volume": "12",
        "pages": "45",
        "doi": "10.1/abc",
    }
    fields.update(over)
    return RefEntry(**fields)


def _scripted(answers):
    """A prompt() stand-in that returns queued answers in order."""
    it = iter(answers)

    def prompt(_msg):
        return next(it)

    return prompt


def _sink():
    lines: list[str] = []
    return lines, lines.append


# --------------------------------------------------------------------------- #
# describe_record
# --------------------------------------------------------------------------- #


def test_describe_record_shows_field_diffs():
    rec = ValidationRecord(
        key="smith2019", status="mismatch", doi="10.1/abc",
        checks=(FieldCheck(field="year", ours="2019", canonical="2020", ok=False),),
    )
    text = "\n".join(describe_record(rec))
    assert "smith2019" in text
    assert "year" in text and "2019" in text and "2020" in text


def test_describe_record_shows_suggested_doi():
    rec = ValidationRecord(key="a", status="doi_suggested", suggested_doi="10.5/x")
    text = "\n".join(describe_record(rec))
    assert "suggested DOI: 10.5/x" in text


# --------------------------------------------------------------------------- #
# review_corrections decision collection
# --------------------------------------------------------------------------- #


def _mismatch_report(canonical):
    return ValidationReport(
        records=(
            ValidationRecord(
                key="smith2019", status="mismatch", doi="10.1/abc",
                checks=(FieldCheck(field="year", ours="2019", canonical="2020", ok=False),),
                canonical_entry=canonical,
            ),
        )
    )


def test_no_flagged_returns_no_decisions():
    report = ValidationReport(
        records=(ValidationRecord(key="ok", status="verified", doi="10.1/a"),)
    )
    _, echo = _sink()
    assert review_corrections([_entry()], report, prompt=_scripted([]), echo=echo) == []


def test_approve_produces_approve_decision():
    report = _mismatch_report(_entry(year="2020"))
    _, echo = _sink()
    decisions = review_corrections(
        [_entry()], report, prompt=_scripted(["a"]), echo=echo
    )
    assert len(decisions) == 1
    assert decisions[0].action == "approve"
    assert decisions[0].key == "smith2019"


def test_deny_and_unrecognized_both_deny():
    report = _mismatch_report(_entry(year="2020"))
    _, echo = _sink()
    for answer in ("d", "xyz"):
        decisions = review_corrections(
            [_entry()], report, prompt=_scripted([answer]), echo=echo
        )
        assert decisions[0].action == "deny"


def test_skip_stops_review_early():
    # Two flagged references; "s" on the first leaves BOTH untouched.
    report = ValidationReport(
        records=(
            ValidationRecord(key="a", status="dead_doi", doi="10.1/x"),
            ValidationRecord(key="b", status="dead_doi", doi="10.1/y"),
        )
    )
    _, echo = _sink()
    decisions = review_corrections(
        [_entry(key="a"), _entry(key="b")], report, prompt=_scripted(["s"]), echo=echo
    )
    assert decisions == []


def test_edit_collects_full_entry_edit():
    report = _mismatch_report(_entry(year="2020"))
    _, echo = _sink()
    # "e" to edit, then one value per EDITABLE_FIELD (title..doi): change year,
    # keep the rest (blank).
    answers = ["e", "", "", "2021", "", "", "", "", ""]
    decisions = review_corrections(
        [_entry()], report, prompt=_scripted(answers), echo=echo
    )
    assert decisions[0].action == "edit"
    assert decisions[0].edited_entry.year == "2021"
    assert decisions[0].edited_entry.title == "A Study of Widgets"  # kept
    assert decisions[0].edited_entry.key == "smith2019"  # identity preserved


def test_edit_can_change_multiple_fields():
    report = _mismatch_report(_entry(year="2020"))
    _, echo = _sink()
    # order: title, authors, year, journal, volume, issue, pages, doi
    answers = ["e", "New Title", "", "", "", "", "", "50-60", "10.9/new"]
    decisions = review_corrections(
        [_entry()], report, prompt=_scripted(answers), echo=echo
    )
    edited = decisions[0].edited_entry
    assert edited.title == "New Title"
    assert edited.pages == "50-60"
    assert edited.doi == "10.9/new"
    assert edited.year == "2019"  # left blank -> current kept
