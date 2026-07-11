"""Tests for the journal template registry (plan item 4).

Covers: manifest loading + validation, discovery, citation-mode resolution,
field-naming error messages on broken manifests, and golden-file rendering of
the revtex4-2 preamble + metadata for a two-author/two-affiliation ``Meta``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latextify.model.meta import Affiliation, Author, Meta
from latextify.templates import loader
from latextify.templates.authors import group_consecutive_by_affiliation
from latextify.templates.loader import ManifestError

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def two_author_meta() -> Meta:
    """Two authors, two affiliations, one of whom (corresponding) spans both.

    Exercises: multiple affiliations per author, a corresponding author with an
    email, and the REVTeX affiliation-grouping loop (the two authors have
    different affiliation sets, so they form two groups).
    """
    return Meta(
        title="Anomalous Transport in a Two-Dimensional Electron Gas",
        authors=(
            Author(
                name="Alice Anderson",
                affiliations=(0, 1),
                email="alice.anderson@university-a.edu",
                corresponding=True,
            ),
            Author(name="Bob Baker", affiliations=(1,)),
        ),
        affiliations=(
            Affiliation("Department of Physics, University A, City A, Country A"),
            Affiliation("National High Field Laboratory, City B, Country B"),
        ),
        abstract=(
            "We report anomalous transport signatures in a two-dimensional "
            "electron gas and analyze their temperature dependence."
        ),
        keywords=("transport", "two-dimensional electron gas"),
    )


# --------------------------------------------------------------------------- #
# Loading + discovery
# --------------------------------------------------------------------------- #


def test_load_revtex_returns_validated_journal():
    j = loader.load("revtex4-2")
    assert j.name == "revtex4-2"
    assert j.document_class == "revtex4-2"
    assert j.class_options == ("aps", "prb", "reprint")
    assert [p.name for p in j.packages][:2] == ["amsmath", "amssymb"]
    assert j.default_mode == "numeric"
    assert j.bib_modes["numeric"].bibstyle == "apsrev4-2"
    assert "authoryear" not in j.bib_modes  # APS is numeric-only
    assert j.metadata_scheme == "revtex"
    assert j.figure_env.single == "figure"
    assert j.figure_env.wide == "figure*"


def test_hyperref_package_carries_options():
    j = loader.load("revtex4-2")
    hyperref = next(p for p in j.packages if p.name == "hyperref")
    assert "colorlinks=true" in hyperref.options


def test_available_lists_revtex():
    assert "revtex4-2" in loader.available()


def test_discover_maps_name_to_manifest():
    found = loader.discover()
    assert "revtex4-2" in found
    assert found["revtex4-2"].name == "manifest.yaml"


# --------------------------------------------------------------------------- #
# Author grouping
# --------------------------------------------------------------------------- #


def test_consecutive_authors_sharing_affiliation_collapse():
    a = Author("Alice", (0,))
    b = Author("Bob", (0,))
    c = Author("Carol", (1,))
    groups = group_consecutive_by_affiliation((a, b, c))
    assert len(groups) == 2
    assert [au.name for au in groups[0].authors] == ["Alice", "Bob"]
    assert groups[0].affiliations == (0,)
    assert [au.name for au in groups[1].authors] == ["Carol"]


# --------------------------------------------------------------------------- #
# Golden-file rendering
# --------------------------------------------------------------------------- #


def test_rendered_preamble_matches_golden():
    j = loader.load("revtex4-2")
    expected = (GOLDEN / "revtex4-2_preamble.tex").read_text(encoding="utf-8")
    assert j.render_preamble() == expected


def test_rendered_metadata_matches_golden():
    j = loader.load("revtex4-2")
    expected = (GOLDEN / "revtex4-2_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


# --------------------------------------------------------------------------- #
# Validation errors — each must name the offending field AND the journal
# --------------------------------------------------------------------------- #


def _write_manifest(tmp_path: Path, name: str, body: str) -> Path:
    jdir = tmp_path / name
    jdir.mkdir()
    (jdir / "manifest.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_broken_manifest_missing_class_names_field(tmp_path):
    body = (
        "bib:\n"
        "  default_mode: numeric\n"
        "  modes:\n"
        "    numeric:\n"
        "      bibstyle: apsrev4-2\n"
        "metadata_scheme: revtex\n"
    )
    root = _write_manifest(tmp_path, "brokenjournal", body)
    with pytest.raises(ManifestError) as exc:
        loader.load("brokenjournal", journals_dir=root)
    msg = str(exc.value)
    assert "class" in msg
    assert "brokenjournal" in msg  # names the journal too


@pytest.mark.parametrize(
    "body, needle",
    [
        # missing bib block
        ("class: foo\nmetadata_scheme: s\n", "bib"),
        # bib with no modes
        ("class: foo\nmetadata_scheme: s\nbib:\n  default_mode: numeric\n  modes: {}\n", "modes"),
        # default_mode not among defined modes
        (
            "class: foo\nmetadata_scheme: s\nbib:\n  default_mode: authoryear\n"
            "  modes:\n    numeric:\n      bibstyle: b\n",
            "default_mode",
        ),
        # mode missing bibstyle
        (
            "class: foo\nmetadata_scheme: s\nbib:\n  default_mode: numeric\n"
            "  modes:\n    numeric: {}\n",
            "bibstyle",
        ),
        # class present but wrong type
        (
            "class: [not, a, string]\nmetadata_scheme: s\nbib:\n  default_mode: numeric\n"
            "  modes:\n    numeric:\n      bibstyle: b\n",
            "class",
        ),
        # package entry without a name
        (
            "class: foo\nmetadata_scheme: s\npackages:\n  - options: [x]\n"
            "bib:\n  default_mode: numeric\n  modes:\n    numeric:\n      bibstyle: b\n",
            "name",
        ),
    ],
)
def test_broken_manifests_raise_named_errors(tmp_path, body, needle):
    root = _write_manifest(tmp_path, "bad", body)
    with pytest.raises(ManifestError) as exc:
        loader.load("bad", journals_dir=root)
    message = str(exc.value)
    assert needle in message
    assert "bad" in message  # journal name is always present


def test_unknown_journal_raises_named_error():
    with pytest.raises(ManifestError) as exc:
        loader.load("no-such-journal-xyz")
    assert "no-such-journal-xyz" in str(exc.value)


def test_unsupported_citation_mode_lists_allowed_modes():
    j = loader.load("revtex4-2")
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="authoryear")
    msg = str(exc.value)
    assert "authoryear" in msg
    assert "numeric" in msg  # names the allowed mode(s)
    assert "revtex4-2" in msg
