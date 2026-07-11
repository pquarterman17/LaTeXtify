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
    guess_meta,
    load_meta,
    load_or_create_meta,
    meta_from_yaml_data,
    render_paper_yaml,
    sidecar_path_for,
)
from latextify.model.meta_sidecar import Author, Meta

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
    assert doe.affiliations == (1,)
    assert doe.corresponding is True
    assert doe.email == "jane.doe@example.edu"
    assert smith.affiliations == (1, 2)
    assert smith.corresponding is False
    assert smith.email is None

    assert meta.affiliations == (
        "Department of Physics, University X, Springfield, USA",
        "Institute of Materials Science, Example City, USA",
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
# YAML rendering with CHECK comments
# --------------------------------------------------------------------------


def test_render_paper_yaml_places_check_comment_before_field_and_round_trips():
    meta = Meta(
        title="Uncertain Title",
        authors=(Author(name="Solo Author"),),
        affiliations=("Some Institute",),
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
    meta = Meta(title="T", authors=(Author(name="A"),), affiliations=("X",))
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
    assert meta.authors[1].affiliations == (1, 2)
