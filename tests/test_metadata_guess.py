"""Tests for latextify.ingest.metadata_guess (plan item 8).

Covers: guess quality against the metadata_titlepage.docx fixture, CHECK-
comment rendering for low-confidence fields, write-once behavior, override
precedence (a hand-edited paper.yaml is never touched), and named-field
schema validation errors.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from latextify.ingest.metadata_guess import (
    MetaGuess,
    MetaValidationError,
    _split_marker_text,
    guess_meta,
    load_meta,
    load_or_create_meta,
    meta_from_yaml_data,
    render_paper_yaml,
    sidecar_path_for,
)
from latextify.model.meta import Affiliation, Author, Meta

FIXTURE = Path(__file__).parent / "fixtures" / "metadata_titlepage.docx"


def _valid_meta_dict() -> dict:
    """A schema-valid paper.yaml payload, safe to mutate per test case."""
    return {
        "title": "A Study of Something",
        "authors": [
            {
                "name": "Ada Lovelace",
                "affiliations": [1],
                "corresponding": True,
                "email": "ada@example.edu",
            },
            {"name": "Alan Turing", "affiliations": [1, 2]},
        ],
        "affiliations": ["Institute A", "Institute B"],
        "abstract": "We study something interesting.",
        "keywords": ["computing", "history"],
    }


# --------------------------------------------------------------------------
# guess quality
# --------------------------------------------------------------------------


def test_guess_quality_on_titlepage_fixture():
    assert FIXTURE.exists(), "run tests/fixtures/make_metadata_titlepage.py to regenerate"
    result = guess_meta(FIXTURE)
    assert isinstance(result, MetaGuess)
    meta = result.meta

    assert meta.title == "Superconducting Gap Anisotropy in Doped Compound X2Y"

    assert [a.name for a in meta.authors] == ["Jane A. Doe", "John B. Smith"]
    doe, smith = meta.authors
    assert doe.affiliations == (0,)
    assert doe.corresponding is True
    assert doe.email == "jane.doe@example.edu"
    assert smith.affiliations == (0, 1)
    assert smith.corresponding is False
    assert smith.email is None

    assert meta.affiliations == (
        Affiliation(name="Department of Physics, University X, Springfield, USA"),
        Affiliation(name="Institute of Materials Science, Example City, USA"),
    )

    assert "magnetometry" in meta.abstract
    assert "anisotropic superconducting gap" in meta.abstract

    assert meta.keywords == (
        "superconductivity",
        "magnetometry",
        "doped compounds",
        "gap anisotropy",
    )

    # A clean, well-marked title page should produce no low-confidence flags.
    assert result.checks == {}


def test_guess_meta_rejects_non_docx(tmp_path):
    """A .txt renamed .docx is not a zip at all; must not leak zipfile.BadZipFile."""
    bogus = tmp_path / "renamed.docx"
    bogus.write_text("This is just plain text, not a docx.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid .docx"):
        guess_meta(bogus)


def test_guess_meta_rejects_docx_missing_document_xml(tmp_path):
    """A valid zip that isn't OOXML (no word/document.xml) must not leak KeyError."""
    import zipfile

    bogus = tmp_path / "notooxml.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("hello.txt", "not a word document")

    with pytest.raises(ValueError, match="not a valid .docx"):
        guess_meta(bogus)


def test_guess_meta_rejects_malformed_document_xml(tmp_path):
    """Malformed XML must not leak a raw lxml.etree.XMLSyntaxError."""
    import zipfile

    bogus = tmp_path / "malformed.docx"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><w:body><w:p>unterminated")

    with pytest.raises(ValueError, match="not a valid .docx"):
        guess_meta(bogus)


def test_corresponding_email_not_stolen_from_abstract_text(tmp_path):
    """A corresponding author with no explicit contact line before the
    abstract must not have an unrelated email mentioned in the abstract body
    (e.g. a data-availability statement) attributed to them -- especially
    when the abstract happens to contain the word "correspondence"."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    title = doc.add_paragraph(style="Title")
    title.add_run("A Study of Something Important")

    authors = doc.add_paragraph()
    authors.add_run("Jane Doe")
    star = authors.add_run("*")
    star.font.superscript = True

    doc.add_paragraph("Department of Physics, University X")

    doc.add_paragraph("Abstract")
    doc.add_paragraph(
        "We study something interesting. In correspondence with a related "
        "dataset, raw data are available upon request from data@example.com."
    )
    doc.add_paragraph("Keywords: physics, science")

    path = tmp_path / "abstract_email.docx"
    doc.save(path)

    result = guess_meta(path)
    author = result.meta.authors[0]
    assert author.corresponding is True
    assert author.email is None, (
        f"abstract email was incorrectly attributed to the corresponding "
        f"author: {author.email!r}"
    )
    assert any("no nearby email" in msg for msg in result.checks.get("authors", []))


def test_corresponding_email_regex_does_not_swallow_trailing_period():
    """A sentence-final period immediately after the email must not be
    captured as part of the address."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    title = doc.add_paragraph(style="Title")
    title.add_run("A Study of Something Important")

    authors = doc.add_paragraph()
    authors.add_run("Jane Doe")
    star = authors.add_run("*")
    star.font.superscript = True

    doc.add_paragraph("Department of Physics, University X")

    corr = doc.add_paragraph()
    marker = corr.add_run("*")
    marker.font.superscript = True
    # Sentence-final period directly after the email, no trailing space.
    corr.add_run("Corresponding author: jane.doe@example.edu.")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: physics, science")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trailing_period.docx"
        doc.save(path)
        result = guess_meta(path)

    author = result.meta.authors[0]
    assert author.email == "jane.doe@example.edu"


def test_guessed_meta_never_references_out_of_range_affiliation(tmp_path):
    """A superscript affiliation marker with no matching affiliation
    paragraph (document jumps straight from the author line to the
    Abstract heading) must not leave the guessed Meta pointing at an
    affiliation index that doesn't exist -- meta_from_yaml_data would
    reject exactly that shape, so an unguarded guess here would crash the
    *next* run once it round-trips through paper.yaml."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of Something")
    authors = doc.add_paragraph()
    authors.add_run("Jane Doe")
    marker = authors.add_run("5")
    marker.font.superscript = True

    doc.add_paragraph("Abstract")
    doc.add_paragraph("Some abstract text.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "dangling_marker.docx"
    doc.save(path)

    result = guess_meta(path)
    author = result.meta.authors[0]

    # No affiliation paragraph existed at all -- the reference must be
    # dropped, never left pointing past the (empty) affiliations tuple.
    assert result.meta.affiliations == ()
    assert all(idx < len(result.meta.affiliations) for idx in author.affiliations)
    assert any("no matching" in msg for msg in result.checks.get("affiliations", []))

    # The guess must itself be valid input to the same schema validator a
    # hand-edited paper.yaml is held to -- prove the full write/reload
    # round trip (what load_or_create_meta does on a second run) succeeds.
    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


# --------------------------------------------------------------------------
# cross-order marker/affiliation linking (item 27)
# --------------------------------------------------------------------------


def _add_superscript(paragraph, text: str) -> None:
    run = paragraph.add_run(text)
    run.font.superscript = True


def test_reversed_numeric_markers_with_labeled_paragraphs_link_by_value(tmp_path):
    """Authors carry markers out of first-seen numeric order ("2" appears
    before "1" reading the author line), but the affiliation paragraphs
    carry their OWN leading markers ("1", "2"). Rule 1 (label-value
    matching) must link each author to the paragraph whose marker matches
    it, not the paragraph that happens to come first physically or the
    marker that happens to be seen first.

    Judged flag-free: every author marker matches exactly one paragraph
    label and every label is referenced back by some author -- there is no
    ambiguity left to surface. The pre-fix code built its mapping from
    first-seen marker order ({"2": 0, "1": 1}) and would have silently
    swapped these two affiliations with zero CHECK flags -- exactly the bug
    this item fixes.
    """
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of Reversed Markers")

    authors = doc.add_paragraph()
    authors.add_run("Ada Lovelace")
    _add_superscript(authors, "2")
    authors.add_run(", Bob Barker")
    _add_superscript(authors, "1")

    aff1 = doc.add_paragraph()
    _add_superscript(aff1, "1")
    aff1.add_run("Institute One")

    aff2 = doc.add_paragraph()
    _add_superscript(aff2, "2")
    aff2.add_run("Institute Two")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "reversed_numeric_labeled.docx"
    doc.save(path)

    result = guess_meta(path)
    ada, bob = result.meta.authors
    affs = result.meta.affiliations

    assert affs[ada.affiliations[0]].name == "Institute Two"
    assert affs[bob.affiliations[0]].name == "Institute One"
    assert result.checks.get("affiliations", []) == []

    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


def test_reversed_numeric_markers_without_labels_link_positionally_by_value(tmp_path):
    """Same reversed-marker author line as above, but this time the
    affiliation paragraphs carry NO leading markers at all. With no labels
    to match against, rule 2 applies: a numeric marker N means "the Nth
    affiliation paragraph" by VALUE (1-based), not by first-seen order --
    marker "2" must resolve to the second paragraph regardless of which
    author's marker was encountered first while scanning the author line.
    """
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of Reversed Markers")

    authors = doc.add_paragraph()
    authors.add_run("Ada Lovelace")
    _add_superscript(authors, "2")
    authors.add_run(", Bob Barker")
    _add_superscript(authors, "1")

    doc.add_paragraph("Institute One")
    doc.add_paragraph("Institute Two")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "reversed_numeric_unlabeled.docx"
    doc.save(path)

    result = guess_meta(path)
    ada, bob = result.meta.authors
    affs = result.meta.affiliations

    assert affs[ada.affiliations[0]].name == "Institute Two"
    assert affs[bob.affiliations[0]].name == "Institute One"

    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


def test_letter_markers_with_labeled_paragraphs_in_swapped_order_link_by_value(tmp_path):
    """Authors are marked in natural first-seen letter order ("a" then
    "b"), but the affiliation paragraphs sit in the OPPOSITE physical
    order ("b"'s paragraph comes first on the page, "a"'s second). Rule 1
    must still link by the paragraph's own marker, not physical position.
    """
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of Swapped Affiliations")

    authors = doc.add_paragraph()
    authors.add_run("Carol Danvers")
    _add_superscript(authors, "a")
    authors.add_run(", Dave Grohl")
    _add_superscript(authors, "b")

    aff_b = doc.add_paragraph()
    _add_superscript(aff_b, "b")
    aff_b.add_run("Dept B")

    aff_a = doc.add_paragraph()
    _add_superscript(aff_a, "a")
    aff_a.add_run("Dept A")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "letter_markers_swapped.docx"
    doc.save(path)

    result = guess_meta(path)
    carol, dave = result.meta.authors
    affs = result.meta.affiliations

    assert affs[carol.affiliations[0]].name == "Dept A"
    assert affs[dave.affiliations[0]].name == "Dept B"
    assert result.checks.get("affiliations", []) == []

    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


def test_marker_referencing_missing_label_drops_reference_and_names_it(tmp_path):
    """An author is marked "3" but no affiliation paragraph is labeled
    "3" (only "1" and "2" exist). The reference must be dropped -- never
    left pointing at a made-up or out-of-range index -- and the CHECK must
    name the offending marker so the author knows exactly what to fix."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of a Missing Label")

    authors = doc.add_paragraph()
    authors.add_run("Eve Adams")
    _add_superscript(authors, "1")
    authors.add_run(", Frank Ocean")
    _add_superscript(authors, "3")

    aff1 = doc.add_paragraph()
    _add_superscript(aff1, "1")
    aff1.add_run("Aff One")

    aff2 = doc.add_paragraph()
    _add_superscript(aff2, "2")
    aff2.add_run("Aff Two")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "missing_label.docx"
    doc.save(path)

    result = guess_meta(path)
    eve, frank = result.meta.authors

    assert result.meta.affiliations[eve.affiliations[0]].name == "Aff One"
    assert frank.affiliations == ()
    assert any(
        "'3'" in msg and "dropped" in msg for msg in result.checks.get("affiliations", [])
    ), result.checks.get("affiliations", [])

    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


def test_nonnumeric_unlabeled_nonsequential_markers_fall_back_with_check(tmp_path):
    """Neither cross-validation signal is available: the markers are
    non-numeric letters and the affiliation paragraphs carry no labels of
    their own. Rule 3 (first-seen-order fallback, the pre-fix behavior)
    is the only option left -- but because the first-seen order ("b" then
    "a") is not already ascending, the mapping is a guess and must be
    flagged rather than emitted with silent confidence."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study of Ambiguous Letters")

    authors = doc.add_paragraph()
    authors.add_run("Grace Hopper")
    _add_superscript(authors, "b")
    authors.add_run(", Heidi Klum")
    _add_superscript(authors, "a")

    doc.add_paragraph("Dept X")
    doc.add_paragraph("Dept Y")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")
    doc.add_paragraph("Keywords: a, b")

    path = tmp_path / "nonsequential_unlabeled.docx"
    doc.save(path)

    result = guess_meta(path)
    grace, heidi = result.meta.authors

    # Reproduces the pre-fix first-seen-order behavior (best effort, no
    # better signal exists) but now it must be flagged.
    assert result.meta.affiliations[grace.affiliations[0]].name == "Dept X"
    assert result.meta.affiliations[heidi.affiliations[0]].name == "Dept Y"
    assert any(
        "marker appearance order" in msg for msg in result.checks.get("affiliations", [])
    )

    rendered = render_paper_yaml(result.meta, result.checks)
    round_tripped = meta_from_yaml_data(yaml.safe_load(rendered))
    assert round_tripped == result.meta


def test_guess_low_confidence_flags_when_cues_are_missing(tmp_path):
    """A docx with no recognizable cues should guess conservatively and flag every field."""
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()
    doc.add_paragraph("Just some plain first paragraph with no styling at all.")
    doc.add_paragraph("Second Author Name Here")
    path = tmp_path / "bare.docx"
    doc.save(path)

    result = guess_meta(path)
    # No Title style, no affiliation markers, no Abstract heading, no Keywords line.
    assert "title" in result.checks
    assert "authors" in result.checks
    assert "abstract" in result.checks
    assert "keywords" in result.checks
    assert result.meta.abstract == ""
    assert result.meta.keywords == ()


# --------------------------------------------------------------------------
# Composite superscript marker tokenization (gap 5: "1*" etc. lost the digit)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The motivating bug: digit + symbol with no separator.
        ("1*", ["1", "*"]),
        # Symbol first.
        ("*1", ["*", "1"]),
        # Other corresponding symbols (dagger, double-dagger, section).
        ("1†", ["1", "†"]),
        ("1‡", ["1", "‡"]),
        ("2§", ["2", "§"]),
        # Letter affiliation marker + symbol.
        ("a*", ["a", "*"]),
        # Bare corresponding symbol only (must NOT invent an affiliation).
        ("*", ["*"]),
        # Separators stay separators, never flags: comma/semicolon/space.
        ("1,2", ["1", "2"]),
        ("1;2", ["1", "2"]),
        ("1, 2", ["1", "2"]),
        # Comma between a digit and a symbol still splits cleanly.
        ("2,†", ["2", "†"]),
        # Multi-digit and sub-affiliation labels stay whole (not over-split).
        ("12", ["12"]),
        ("1a", ["1a"]),
        # Empty / whitespace-only runs yield nothing.
        ("", []),
        ("  ", []),
    ],
)
def test_split_marker_text_tokenizes_composites(text, expected):
    assert _split_marker_text(text) == expected


def test_composite_superscript_without_comma_keeps_affiliation_and_corresponding(tmp_path):
    """The YIG-paper bug: an author superscript run typed as a single "1*"
    (no comma between the affiliation digit and the corresponding symbol).

    The pre-fix tokenizer split only on commas/whitespace, so "1*" stayed one
    token that failed ``isalnum()`` -- the author was flagged corresponding
    but lost affiliation 1 entirely (which then mis-attached to the next
    affiliation block under REVTeX). Both parts must now survive.
    """
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()

    doc.add_paragraph(style="Title").add_run("A Study With Composite Markers")

    authors = doc.add_paragraph()
    authors.add_run("Ada Lovelace")
    _add_superscript(authors, "1*")  # composite, no comma
    authors.add_run(", Bob Barker")
    _add_superscript(authors, "2")

    aff1 = doc.add_paragraph()
    _add_superscript(aff1, "1")
    aff1.add_run("Institute One")

    aff2 = doc.add_paragraph()
    _add_superscript(aff2, "2")
    aff2.add_run("Institute Two")

    corr = doc.add_paragraph()
    _add_superscript(corr, "*")
    corr.add_run("Corresponding author: ada@example.edu")

    doc.add_paragraph("Abstract")
    doc.add_paragraph("We study something interesting.")

    path = tmp_path / "composite_marker.docx"
    doc.save(path)

    result = guess_meta(path)
    ada, bob = result.meta.authors
    affs = result.meta.affiliations

    # Ada keeps BOTH: affiliation 1 AND corresponding.
    assert affs[ada.affiliations[0]].name == "Institute One"
    assert ada.corresponding is True
    assert ada.email == "ada@example.edu"
    # Bob is unaffected and correctly linked to Institute Two.
    assert affs[bob.affiliations[0]].name == "Institute Two"
    assert bob.corresponding is False

    # A valid, in-range Meta that round-trips through paper.yaml validation.
    rendered = render_paper_yaml(result.meta, result.checks)
    assert meta_from_yaml_data(yaml.safe_load(rendered)) == result.meta


# --------------------------------------------------------------------------
# YAML rendering with CHECK comments
# --------------------------------------------------------------------------


def test_render_paper_yaml_places_check_comment_before_field_and_round_trips():
    meta = Meta(
        title="Uncertain Title",
        authors=(Author(name="Solo Author"),),
        affiliations=(Affiliation(name="Some Institute"),),
        abstract="",
        keywords=(),
    )
    checks = {
        "title": ["guessed from the largest-font paragraph; verify."],
        "abstract": ["no 'Abstract' heading found; abstract left empty."],
        "keywords": ["no 'Keywords:' line found; keywords left empty."],
    }
    text = render_paper_yaml(meta, checks)
    lines = text.splitlines()

    title_check_idx = lines.index("# CHECK: guessed from the largest-font paragraph; verify.")
    title_idx = next(i for i, line in enumerate(lines) if line.startswith("title:"))
    assert title_check_idx == title_idx - 1

    abstract_check_idx = lines.index("# CHECK: no 'Abstract' heading found; abstract left empty.")
    abstract_idx = next(i for i, line in enumerate(lines) if line.startswith("abstract:"))
    assert abstract_check_idx == abstract_idx - 1

    # No check was requested for 'authors' or 'affiliations' -- no comment should precede them.
    authors_idx = next(i for i, line in enumerate(lines) if line.startswith("authors:"))
    assert not lines[authors_idx - 1].startswith("# CHECK")

    # Comments are valid YAML and round-trip through the same validator used for hand-edited files.
    round_tripped = meta_from_yaml_data(yaml.safe_load(text))
    assert round_tripped == meta


def test_render_paper_yaml_no_comments_when_no_checks():
    meta = Meta(title="T", authors=(Author(name="A"),), affiliations=(Affiliation(name="X"),))
    text = render_paper_yaml(meta, {})
    assert "# CHECK" not in text


# --------------------------------------------------------------------------
# write-once behavior + override precedence
# --------------------------------------------------------------------------


def test_load_or_create_writes_sidecar_once(tmp_path):
    docx_path = tmp_path / "paper.docx"
    shutil.copyfile(FIXTURE, docx_path)
    sidecar = sidecar_path_for(docx_path)

    assert not sidecar.exists()
    meta1 = load_or_create_meta(docx_path)
    assert sidecar.exists()
    assert meta1.title == "Superconducting Gap Anisotropy in Doped Compound X2Y"

    original_text = sidecar.read_text(encoding="utf-8")

    # Hand-edit the sidecar with a completely different, schema-valid payload.
    sentinel = _valid_meta_dict()
    sentinel["title"] = "HAND EDITED SENTINEL TITLE"
    sidecar.write_text(yaml.safe_dump(sentinel, sort_keys=False), encoding="utf-8")
    hand_edited_text = sidecar.read_text(encoding="utf-8")
    assert hand_edited_text != original_text

    # A second run must return the hand-edited content, not re-guess or overwrite it.
    meta2 = load_or_create_meta(docx_path)
    assert meta2.title == "HAND EDITED SENTINEL TITLE"
    assert [a.name for a in meta2.authors] == ["Ada Lovelace", "Alan Turing"]
    assert sidecar.read_text(encoding="utf-8") == hand_edited_text


def test_load_or_create_respects_explicit_sidecar_path(tmp_path):
    docx_path = tmp_path / "paper.docx"
    shutil.copyfile(FIXTURE, docx_path)
    custom_sidecar = tmp_path / "custom_meta.yaml"

    assert not custom_sidecar.exists()
    load_or_create_meta(docx_path, sidecar_path=custom_sidecar)
    assert custom_sidecar.exists()
    assert not sidecar_path_for(docx_path).exists()


# --------------------------------------------------------------------------
# schema validation errors (named fields)
# --------------------------------------------------------------------------


def test_missing_title_names_field():
    data = _valid_meta_dict()
    del data["title"]
    with pytest.raises(MetaValidationError, match=r"missing required field 'title'"):
        meta_from_yaml_data(data)


def test_non_string_title_names_field():
    data = _valid_meta_dict()
    data["title"] = 42
    with pytest.raises(MetaValidationError, match=r"field 'title'"):
        meta_from_yaml_data(data)


def test_authors_not_a_list_names_field():
    data = _valid_meta_dict()
    data["authors"] = "not a list"
    with pytest.raises(MetaValidationError, match=r"field 'authors'"):
        meta_from_yaml_data(data)


def test_author_missing_name_names_field():
    data = _valid_meta_dict()
    del data["authors"][0]["name"]
    with pytest.raises(MetaValidationError, match=r"authors\[0\]\.name"):
        meta_from_yaml_data(data)


def test_author_affiliation_out_of_range_names_field():
    data = _valid_meta_dict()
    data["authors"][0]["affiliations"] = [99]
    with pytest.raises(MetaValidationError, match=r"authors\[0\]\.affiliations\[0\]"):
        meta_from_yaml_data(data)


def test_author_affiliation_not_int_names_field():
    data = _valid_meta_dict()
    data["authors"][0]["affiliations"] = ["one"]
    with pytest.raises(MetaValidationError, match=r"authors\[0\]\.affiliations\[0\]"):
        meta_from_yaml_data(data)


def test_author_corresponding_not_bool_names_field():
    data = _valid_meta_dict()
    data["authors"][0]["corresponding"] = "yes"
    with pytest.raises(MetaValidationError, match=r"authors\[0\]\.corresponding"):
        meta_from_yaml_data(data)


def test_missing_affiliations_names_field():
    data = _valid_meta_dict()
    del data["affiliations"]
    with pytest.raises(MetaValidationError, match=r"missing required field 'affiliations'"):
        meta_from_yaml_data(data)


def test_affiliation_entry_not_string_names_field():
    data = _valid_meta_dict()
    data["affiliations"] = [123]
    with pytest.raises(MetaValidationError, match=r"affiliations\[0\]"):
        meta_from_yaml_data(data)


def test_keyword_not_string_names_field():
    data = _valid_meta_dict()
    data["keywords"] = ["ok", 5]
    with pytest.raises(MetaValidationError, match=r"keywords\[1\]"):
        meta_from_yaml_data(data)


def test_root_not_a_mapping():
    with pytest.raises(MetaValidationError, match=r"root must be a mapping"):
        meta_from_yaml_data(["not", "a", "mapping"])


def test_load_meta_invalid_yaml_syntax_raises_named_error(tmp_path):
    bad = tmp_path / "paper.yaml"
    bad.write_text("title: [unterminated\n", encoding="utf-8")
    with pytest.raises(MetaValidationError, match=r"invalid YAML syntax"):
        load_meta(bad)


def test_load_meta_valid_file_round_trips(tmp_path):
    good = tmp_path / "paper.yaml"
    data = _valid_meta_dict()
    good.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    meta = load_meta(good)
    assert meta.title == "A Study of Something"
    assert meta.authors[0].name == "Ada Lovelace"
    assert meta.authors[0].corresponding is True
    assert meta.authors[0].email == "ada@example.edu"
    assert meta.authors[1].affiliations == (0, 1)  # YAML 1-based -> IR 0-based
