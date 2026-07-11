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

Item 10 (elsarticle) adds: dual citation-mode preamble rendering (numeric +
authoryear with natbib options folded into the class options), elsarticle
frontmatter metadata with affiliation indices and corresponding-author markup,
and a Tectonic compile test that proves the vendored v3.5 class shadows the
bundle's broken v3.3.
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
# Citation style switching (plan item 18)
# --------------------------------------------------------------------------- #


def test_elsarticle_dual_mode_preambles_differ():
    """Verify that elsarticle's numeric and authoryear modes render different preambles."""
    j = loader.load("elsarticle")
    numeric_preamble = j.render_preamble(mode="numeric")
    authoryear_preamble = j.render_preamble(mode="authoryear")

    # Both should render, but the bibstyles should differ
    assert "elsarticle-num" in numeric_preamble
    assert "elsarticle-harv" in authoryear_preamble
    assert "elsarticle-num" not in authoryear_preamble
    assert "elsarticle-harv" not in numeric_preamble


def test_elsarticle_natbib_options_in_numeric_mode():
    """Numeric mode should have 'numbers' in natbib options (as class option)."""
    j = loader.load("elsarticle")
    numeric_preamble = j.render_preamble(mode="numeric")
    # elsarticle folds natbib options into \documentclass
    assert "numbers" in numeric_preamble


def test_elsarticle_natbib_options_in_authoryear_mode():
    """Authoryear mode should have 'authoryear' in natbib options (as class option)."""
    j = loader.load("elsarticle")
    authoryear_preamble = j.render_preamble(mode="authoryear")
    # elsarticle folds natbib options into \documentclass
    assert "authoryear" in authoryear_preamble


def test_ieeetran_unsupported_authoryear_mode():
    """IEEE Transactions only supports numeric mode."""
    j = loader.load("ieeetran")
    assert "authoryear" not in j.bib_modes
    assert "numeric" in j.bib_modes
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="authoryear")
    msg = str(exc.value)
    assert "authoryear" in msg
    assert "numeric" in msg
    assert "ieeetran" in msg


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


# --------------------------------------------------------------------------- #
# elsarticle journal folder (plan item 10)
# --------------------------------------------------------------------------- #


def test_available_lists_elsarticle():
    assert "elsarticle" in loader.available()


def test_load_elsarticle_returns_validated_journal():
    j = loader.load("elsarticle")
    assert j.name == "elsarticle"
    assert j.document_class == "elsarticle"
    assert j.class_options == ("review",)
    assert [p.name for p in j.packages][:2] == ["amsmath", "amssymb"]
    assert j.default_mode == "numeric"
    assert j.bib_modes["numeric"].bibstyle == "elsarticle-num"
    assert j.bib_modes["numeric"].natbib_options == ("numbers",)
    assert j.bib_modes["authoryear"].bibstyle == "elsarticle-harv"
    assert j.bib_modes["authoryear"].natbib_options == ("authoryear",)
    assert j.metadata_scheme == "elsarticle"
    assert j.figure_env.single == "figure"
    assert j.figure_env.wide == "figure*"
    # v3.5 of the class is vendored to shadow the broken v3.3 in the bundle.
    assert j.vendor == ("vendor/elsarticle.cls",)
    assert (j.root / "vendor" / "elsarticle.cls").is_file()


def test_rendered_elsarticle_preamble_numeric_matches_golden():
    j = loader.load("elsarticle")
    expected = (GOLDEN / "elsarticle_preamble_numeric.tex").read_text(encoding="utf-8")
    assert j.render_preamble(mode="numeric") == expected


def test_rendered_elsarticle_preamble_authoryear_matches_golden():
    j = loader.load("elsarticle")
    expected = (GOLDEN / "elsarticle_preamble_authoryear.tex").read_text(encoding="utf-8")
    assert j.render_preamble(mode="authoryear") == expected


def test_rendered_elsarticle_metadata_matches_golden():
    j = loader.load("elsarticle")
    expected = (GOLDEN / "elsarticle_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


@requires_tectonic
@skip_without_tectonic
@pytest.mark.parametrize("mode", ["numeric", "authoryear"])
def test_elsarticle_document_compiles(tmp_path, mode):
    """Rendered elsarticle preamble + frontmatter compiles to a real PDF.

    The document is assembled exactly like the emitter's main.tex: preamble
    before ``\\begin{document}``, metadata (the frontmatter environment) after
    it. The journal's vendored elsarticle.cls v3.5 is staged via
    ``compile_document(vendor_dir=...)`` — the bundle's own v3.3 fails at
    ``\\maketitle`` with an undefined ``env/\\elsarticletitlealign/before``
    hook, which v3.5 fixed (see the manifest's vendor note).
    """
    j = loader.load("elsarticle")
    tex_content = (
        j.render_preamble(mode=mode)
        + "\\begin{document}\n"
        + j.render_metadata(two_author_meta())
        + "This is a test document.\n"
        + "\\end{document}\n"
    )

    tex_file = tmp_path / "test.tex"
    tex_file.write_text(tex_content, encoding="utf-8")

    result = compile_document(
        tex_file,
        tectonic_path=ensure_tectonic(),
        vendor_dir=j.root / "vendor",
    )

    assert result.success, (
        f"Compilation failed (mode={mode}):\n"
        f"Return code: {result.returncode}\n"
        f"Log:\n{result.raw_log}"
    )
    assert result.pdf_path is not None
    assert result.pdf_path.is_file()
    # Prove the vendored v3.5 (not the bundle's v3.3) was actually used.
    assert "2026/01/09, 3.5: Elsevier Ltd" in result.raw_log


# --------------------------------------------------------------------------- #
# Metadata LaTeX escaping -- specials in real titles must not break compilation
# --------------------------------------------------------------------------- #

ALL_JOURNALS = ["revtex4-2", "ieeetran", "elsarticle", "sn-jnl"]


def nasty_meta() -> Meta:
    """A Meta whose text fields carry every LaTeX special plus unicode.

    Models real manuscript metadata like ``Effect of 5% doping & strain`` -- the
    raw specials (``& % $ # _ { } ~ ^ \\``) would break compilation if emitted
    verbatim; unicode (accent, CJK) must survive untouched (UTF-8 + XeTeX).
    """
    return Meta(
        title="Effect of 5% doping & strain on H_2O #1 {x} ~a ^b \\d",
        authors=(
            Author(
                name="Ann O'Néil & Co.",
                affiliations=(0,),
                email="a_b@x.edu",
                corresponding=True,
            ),
        ),
        affiliations=(Affiliation("R&D Lab #3, 100% Institute, 中文系"),),
        abstract="We show 50% > 30% & p_c < 1 at cost $5 (see _note_).",
        keywords=("a&b", "c_d"),
    )


@pytest.mark.parametrize("journal_name", ALL_JOURNALS)
def test_metadata_escapes_latex_specials(journal_name):
    j = loader.load(journal_name)
    out = j.render_metadata(nasty_meta())

    # The raw, unescaped title fragment must NOT survive.
    assert "5% doping & strain" not in out
    # Each special is neutralized.
    assert "5\\% doping \\& strain" in out
    assert "H\\_2O \\#1 \\{x\\}" in out
    assert "\\textasciitilde{}a \\textasciicircum{}b \\textbackslash{}d" in out
    # No bare specials remain in the title line specifically.
    title_line = next(line for line in out.splitlines() if "doping" in line)
    for bare in ("& ", " % ", "$5"):
        assert bare not in title_line.replace("\\&", "").replace("\\%", "").replace("\\$", "")


@pytest.mark.parametrize("journal_name", ALL_JOURNALS)
def test_metadata_preserves_unicode(journal_name):
    j = loader.load(journal_name)
    out = j.render_metadata(nasty_meta())
    # Accents and CJK pass through untouched (output is UTF-8; XeTeX handles it).
    assert "O'Néil" in out
    assert "中文系" in out


def test_render_metadata_does_not_mutate_the_ir():
    # Escaping happens on a copy at the rendering boundary; the caller's Meta
    # must stay raw for every other consumer.
    meta = nasty_meta()
    loader.load("revtex4-2").render_metadata(meta)
    assert meta.title == "Effect of 5% doping & strain on H_2O #1 {x} ~a ^b \\d"
    assert meta.authors[0].name == "Ann O'Néil & Co."
    assert meta.affiliations[0].name == "R&D Lab #3, 100% Institute, 中文系"


@requires_tectonic
@skip_without_tectonic
def test_metadata_with_specials_compiles_under_revtex(tmp_path):
    """A specials-laden title/abstract compiles once escaped (regression for the
    unescaped-metadata bug that broke real titles like "5% doping & strain")."""
    j = loader.load("revtex4-2")
    tex = (
        j.render_preamble()
        + "\\begin{document}\n"
        + j.render_metadata(nasty_meta())
        + "Body text.\n"
        + "\\end{document}\n"
    )
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(tex, encoding="utf-8")

    result = compile_document(tex_path, tectonic_path=ensure_tectonic())
    assert result.success, result.raw_log
    assert result.pdf_path is not None and result.pdf_path.is_file()
