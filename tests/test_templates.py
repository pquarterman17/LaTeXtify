"""Tests for the journal template registry (plan item 4) and journal folders.

Covers: manifest loading + validation, discovery, citation-mode resolution,
field-naming error messages on broken manifests, and golden-file rendering of
the revtex4-2 preamble + metadata for a two-author/two-affiliation ``Meta``.

Also covers the IEEEtran journal folder (plan item 11): its manifest declares
only the numeric bib mode (no authoryear), and its metadata template groups
authors *globally* by affiliation set (``group_globally_by_affiliation``,
registered as the ``group_authors_global`` Jinja global) rather than by
consecutive run -- see the 3-author/2-affiliation golden case below, where
authors 1 and 3 share an affiliation but author 2 sits between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.model.meta import Affiliation, Author, Meta
from latextify.templates import loader
from latextify.templates.authors import (
    group_consecutive_by_affiliation,
    group_globally_by_affiliation,
)
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


# --------------------------------------------------------------------------- #
# IEEEtran journal folder (plan item 11)
# --------------------------------------------------------------------------- #


def three_author_two_affiliation_meta() -> Meta:
    """3 authors / 2 affiliations, authors 1 and 3 sharing an affiliation.

    Bob (author 2) sits *between* Alice and Carol in document order but does
    not share their affiliation -- this is the case that distinguishes
    IEEEtran's global affiliation-set grouping from REVTeX/elsarticle's
    consecutive-run grouping: Alice and Carol must still land in one
    ``\\IEEEauthorblockN``/``\\IEEEauthorblockA`` pair despite not being
    adjacent.
    """
    return Meta(
        title="Global Author-Block Grouping in a Two-Column World",
        authors=(
            Author(
                name="Alice Anderson",
                affiliations=(0,),
                email="alice.anderson@university-a.edu",
                corresponding=True,
            ),
            Author(name="Bob Baker", affiliations=(1,)),
            Author(name="Carol Chen", affiliations=(0,)),
        ),
        affiliations=(
            Affiliation(
                "Department of Electrical Engineering, University A, City A, Country A"
            ),
            Affiliation("Signal Processing Laboratory, Institute B, City B, Country B"),
        ),
        abstract=(
            "We demonstrate globally grouped IEEE author blocks across "
            "non-consecutive authors sharing an affiliation."
        ),
        keywords=("author blocks", "IEEEtran"),
    )


def test_load_ieeetran_returns_validated_journal():
    j = loader.load("ieeetran")
    assert j.name == "ieeetran"
    assert j.document_class == "IEEEtran"
    assert j.class_options == ("journal",)
    assert j.default_mode == "numeric"
    assert j.bib_modes["numeric"].bibstyle == "IEEEtran"
    assert j.metadata_scheme == "ieeetran"
    assert j.figure_env.single == "figure"
    assert j.figure_env.wide == "figure*"


def test_ieeetran_manifest_declares_only_numeric_mode():
    """The mode-omission path (plan item 18): IEEEtran.bst has no authoryear."""
    j = loader.load("ieeetran")
    assert set(j.bib_modes) == {"numeric"}
    assert "authoryear" not in j.bib_modes


def test_ieeetran_unsupported_authoryear_mode_errors():
    j = loader.load("ieeetran")
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="authoryear")
    msg = str(exc.value)
    assert "authoryear" in msg
    assert "numeric" in msg  # names the (only) allowed mode
    assert "ieeetran" in msg


def test_available_lists_ieeetran():
    assert "ieeetran" in loader.available()


def test_discover_maps_ieeetran_name_to_manifest():
    found = loader.discover()
    assert "ieeetran" in found
    assert found["ieeetran"].name == "manifest.yaml"


# --------------------------------------------------------------------------- #
# Global (non-consecutive) author grouping
# --------------------------------------------------------------------------- #


def test_globally_grouped_authors_merge_non_adjacent_matches():
    a = Author("Alice", (0,))
    b = Author("Bob", (1,))
    c = Author("Carol", (0,))
    groups = group_globally_by_affiliation((a, b, c))
    assert len(groups) == 2
    assert [au.name for au in groups[0].authors] == ["Alice", "Carol"]
    assert groups[0].affiliations == (0,)
    assert [au.name for au in groups[1].authors] == ["Bob"]
    assert groups[1].affiliations == (1,)


def test_globally_grouped_authors_order_by_first_appearance():
    """Group order follows each affiliation key's first appearance, not size."""
    a = Author("Alice", (1,))
    b = Author("Bob", (0,))
    c = Author("Carol", (1,))
    groups = group_globally_by_affiliation((a, b, c))
    assert [g.affiliations for g in groups] == [(1,), (0,)]


# --------------------------------------------------------------------------- #
# Golden-file rendering
# --------------------------------------------------------------------------- #


def test_rendered_ieeetran_preamble_matches_golden():
    j = loader.load("ieeetran")
    expected = (GOLDEN / "ieeetran_preamble.tex").read_text(encoding="utf-8")
    assert j.render_preamble() == expected


def test_rendered_ieeetran_metadata_matches_golden():
    j = loader.load("ieeetran")
    expected = (GOLDEN / "ieeetran_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(three_author_two_affiliation_meta()) == expected


def test_rendered_ieeetran_metadata_groups_globally_not_consecutively():
    """Belt-and-suspenders check directly on the rendered text (not just the
    golden file): Alice and Carol's names appear together in one
    \\IEEEauthorblockN even though Bob is emitted between them in document
    order, and Bob gets his own block.
    """
    j = loader.load("ieeetran")
    rendered = j.render_metadata(three_author_two_affiliation_meta())
    assert r"\IEEEauthorblockN{Alice Anderson, Carol Chen}" in rendered
    assert r"\IEEEauthorblockN{Bob Baker}" in rendered
    # The shared affiliation appears once, not once per author in the group.
    assert rendered.count("Department of Electrical Engineering") == 1
    # The corresponding author's email lands inside the (shared) affiliation
    # block -- IEEE has no dedicated \correspondingauthor-style macro.
    assert "alice.anderson@university-a.edu" in rendered


# --------------------------------------------------------------------------- #
# Real Tectonic compile -- is IEEEtran.cls in the Tectonic bundle?
# --------------------------------------------------------------------------- #


def _tectonic_available() -> bool:
    try:
        ensure_tectonic()
        return True
    except Exception:
        return False


_TECTONIC_AVAILABLE = _tectonic_available()

requires_tectonic = pytest.mark.tectonic
skip_without_tectonic = pytest.mark.skipif(
    not _TECTONIC_AVAILABLE,
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)


@requires_tectonic
@skip_without_tectonic
def test_ieeetran_rendered_project_compiles_via_tectonic(tmp_path):
    """The de-risk gate for item 11: does Tectonic's bundle provide IEEEtran.cls?

    Renders the real manifest-driven preamble + metadata (not a hand-written
    minimal doc) so this exercises the actual journal folder, then compiles
    it end-to-end. If IEEEtran.cls is missing from the bundle, this fails with
    a "file not found" diagnostic rather than vendoring silently -- see the
    executor report for the outcome.
    """
    j = loader.load("ieeetran")
    preamble = j.render_preamble()
    metadata = j.render_metadata(three_author_two_affiliation_meta())

    tex = (
        preamble
        + "\\begin{document}\n"
        + metadata
        + "Hello, IEEE world!\n"
        + "\\end{document}\n"
    )
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(tex, encoding="utf-8")

    vendor_dir = (
        Path(__file__).parent.parent
        / "latextify" / "templates" / "journals" / "ieeetran" / "vendor"
    )
    result = compile_document(tex_path, vendor_dir=vendor_dir if vendor_dir.is_dir() else None)

    assert result.success, result.raw_log
    assert result.pdf_path is not None
    assert result.pdf_path.is_file()
    assert result.pdf_path.stat().st_size > 0
