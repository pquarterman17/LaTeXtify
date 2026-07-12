"""Tests for the three plan-item-22 journal folders: achemso, iopart, wiley.

A separate file from ``test_templates.py`` on purpose (same rationale as
``test_templates_sn_jnl.py``): this item adds three journal folders at once,
each with its own real-compile proof, so they live together here rather than
growing the shared file. The generic escaping/edge-case tests (LaTeX-special
escaping, out-of-range affiliation indices, zero-author rendering, etc.) are
covered for all three via ``test_templates.py``'s ``ALL_JOURNALS`` list
(additive one-line edit, same pattern sn-jnl used) -- not duplicated here.

Per-journal outcome (see each manifest.yaml for the full writeup):

* achemso -- IN the Tectonic bundle, no vendoring. Its ``\\title``/``\\author``/
  ``\\affiliation``/``\\email``/``\\keywords`` are ``\\@onlypreamble`` commands
  that MUST run before ``\\begin{document}``, which conflicts with this repo's
  fixed ``main.tex`` (always ``\\input``s the metadata file AFTER
  ``\\begin{document}``). Worked around entirely within the template files
  (preamble.tex.j2 re-``\\input``s the metadata file early; metadata.tex.j2
  tells the two reads apart with an ``\\ifdefined`` guard) -- see
  ``TestAchemsoCompile`` below, which reproduces the exact
  generated/preamble.tex + generated/metadata.tex + main.tex file layout
  ``emit_project()`` writes to prove the trick works for real.
* iopart -- NOT in the Tectonic bundle; LPPL-licensed (IOP Publishing Ltd,
  verified across two independent mirrors, byte-identical) -- vendored
  (iopart.cls + its two size-option .clo files). Also needed a documented
  workaround for iopart.cls's own ``equation*``/amsmath clash (see
  manifest.yaml + VENDOR_LICENSE.txt). ``TestIopartCompile`` proves the
  vendored files compile AND that the vendored companion-BST situation is
  inverted from sn-jnl's: iopart-num.bst IS in the bundle, only the class
  needs vendoring.
* wiley -- NOT in the Tectonic bundle; WileyNJD-v2.cls carries an explicit
  "copyright by SPi Technologies Ltd. All rights reserved" header (NOT LPPL,
  NOT freely redistributable) -- deliberately NOT vendored. Compile tests are
  an unconditional skip (see ``test_wiley_compile_is_documented_skip``); a
  negative-control test proves the missing-class diagnostic is actionable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from latextify.compile.tectonic import (
    TectonicNotAvailableError,
    compile_document,
    ensure_tectonic,
    stage_vendor_files,
)
from latextify.model.meta import Affiliation, Author, Meta
from latextify.templates import loader
from latextify.templates.authors import format_iopart_superscript
from latextify.templates.loader import ManifestError, MetadataError

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def _code_lines(rendered: str) -> list[str]:
    """Rendered text with ``%``-comment lines dropped -- several assertions
    below need to distinguish real LaTeX output from this file's own heavily
    documented ``%`` comments, which quote the very macro names/strings being
    asserted about (e.g. ``\\author{}`` appears literally in a comment
    explaining the ``\\author{}`` convention)."""
    return [line for line in rendered.splitlines() if not line.lstrip().startswith("%")]


def two_author_meta() -> Meta:
    """Two authors, two affiliations, one corresponding author spanning both.

    Mirrors ``test_templates.py``'s and ``test_templates_sn_jnl.py``'s fixture
    of the same name so all journal folders are directly comparable.
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


def _tectonic_available() -> bool:
    try:
        ensure_tectonic()
        return True
    except TectonicNotAvailableError:
        return False


requires_tectonic = pytest.mark.tectonic
skip_without_tectonic = pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)


# --------------------------------------------------------------------------- #
# format_iopart_superscript (authors.py addition)
# --------------------------------------------------------------------------- #


def test_format_iopart_superscript_converts_0_based_to_1_based_superscript():
    assert format_iopart_superscript((0, 1)) == "$^{1,2}$"
    assert format_iopart_superscript((2,)) == "$^{3}$"
    assert format_iopart_superscript(()) == ""


# --------------------------------------------------------------------------- #
# Discovery + loading
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("journal_name", ["achemso", "iopart", "wiley"])
def test_available_lists_journal(journal_name):
    assert journal_name in loader.available()


def test_load_achemso_returns_validated_journal():
    j = loader.load("achemso")
    assert j.name == "achemso"
    assert j.document_class == "achemso"
    assert j.class_options == ("journal=jacsat", "manuscript=article")
    assert [p.name for p in j.packages] == [
        "amsmath",
        "amssymb",
        "bm",
        "booktabs",
        "hyperref",
    ]
    assert j.default_mode == "numeric"
    assert set(j.bib_modes) == {"numeric"}
    assert j.bib_modes["numeric"].bibstyle == "achemso"
    assert j.metadata_scheme == "achemso"
    assert j.vendor == ()


def test_load_iopart_returns_validated_journal():
    j = loader.load("iopart")
    assert j.name == "iopart"
    assert j.document_class == "iopart"
    assert j.class_options == ("12pt",)
    assert j.default_mode == "numeric"
    assert set(j.bib_modes) == {"numeric"}
    assert j.bib_modes["numeric"].bibstyle == "iopart-num"
    assert j.metadata_scheme == "iopart"
    assert set(j.vendor) == {"iopart.cls", "iopart10.clo", "iopart12.clo"}
    for name in j.vendor:
        assert (j.root / "vendor" / name).is_file()


def test_load_wiley_returns_validated_journal():
    j = loader.load("wiley")
    assert j.name == "wiley"
    assert j.document_class == "WileyNJD-v2"
    assert j.default_mode == "numeric"
    assert set(j.bib_modes) == {"numeric", "authoryear"}
    assert j.bib_modes["numeric"].bibstyle == "WileyNJD-AMS"
    assert j.bib_modes["authoryear"].bibstyle == "WileyNJD-Harvard"
    assert j.metadata_scheme == "wiley"
    # Deliberately not vendored -- see manifest.yaml's LICENSE FINDING.
    assert j.vendor == ()
    assert not (j.root / "vendor").exists()


def test_achemso_manifest_declares_only_numeric_mode():
    j = loader.load("achemso")
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="authoryear")
    msg = str(exc.value)
    assert "authoryear" in msg
    assert "numeric" in msg
    assert "achemso" in msg


def test_iopart_manifest_declares_only_numeric_mode():
    j = loader.load("iopart")
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="authoryear")
    msg = str(exc.value)
    assert "authoryear" in msg
    assert "numeric" in msg
    assert "iopart" in msg


# --------------------------------------------------------------------------- #
# Golden-file rendering
# --------------------------------------------------------------------------- #


def test_rendered_achemso_preamble_matches_golden():
    j = loader.load("achemso")
    expected = (GOLDEN / "achemso_preamble.tex").read_text(encoding="utf-8")
    assert j.render_preamble() == expected


def test_rendered_achemso_metadata_matches_golden():
    j = loader.load("achemso")
    expected = (GOLDEN / "achemso_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


def test_achemso_metadata_no_bibliographystyle_line():
    """The manifest's ARCHITECTURE FINDING: achemso must never get an explicit
    \\bibliographystyle line -- the class injects its own into the .aux and a
    second, explicit one is a real BibTeX error (verified during development)."""
    j = loader.load("achemso")
    code_lines = _code_lines(j.render_preamble())
    assert not any(line.startswith("\\bibliographystyle") for line in code_lines)


def test_rendered_iopart_preamble_matches_golden():
    j = loader.load("iopart")
    expected = (GOLDEN / "iopart_preamble.tex").read_text(encoding="utf-8")
    assert j.render_preamble() == expected


def test_rendered_iopart_metadata_matches_golden():
    j = loader.load("iopart")
    expected = (GOLDEN / "iopart_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


def test_iopart_metadata_uses_single_author_call_with_superscripts():
    """Direct assertion of the plan's done-when: one \\author{} call with every
    name comma-joined and per-author superscript refs baked into the text."""
    j = loader.load("iopart")
    rendered = j.render_metadata(two_author_meta())
    assert "\\author{Alice Anderson$^{1,2}$, Bob Baker$^{2}$}" in rendered
    assert "\\address{$^{1}$ Department of Physics, University A, City A, Country A}" in rendered
    assert "\\address{$^{2}$ National High Field Laboratory, City B, Country B}" in rendered
    assert "\\ead{alice.anderson@university-a.edu}" in rendered
    # Only one \author{...} call, not one per author.
    code_lines = _code_lines(rendered)
    assert sum(1 for line in code_lines if line.startswith("\\author{")) == 1


def test_rendered_wiley_preamble_numeric_matches_golden():
    j = loader.load("wiley")
    expected = (GOLDEN / "wiley_preamble_numeric.tex").read_text(encoding="utf-8")
    assert j.render_preamble(mode="numeric") == expected


def test_rendered_wiley_preamble_authoryear_matches_golden():
    j = loader.load("wiley")
    expected = (GOLDEN / "wiley_preamble_authoryear.tex").read_text(encoding="utf-8")
    assert j.render_preamble(mode="authoryear") == expected


def test_rendered_wiley_metadata_matches_golden():
    j = loader.load("wiley")
    expected = (GOLDEN / "wiley_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


def test_wiley_metadata_uses_starred_author_for_corresponding_only():
    j = loader.load("wiley")
    rendered = j.render_metadata(two_author_meta())
    assert "\\author*[1,2]{Alice Anderson}" in rendered  # corresponding
    assert "\\author[2]{Bob Baker}" in rendered  # not corresponding
    assert "\\address[1]{Department of Physics, University A, City A, Country A}" in rendered
    assert "\\address[2]{National High Field Laboratory, City B, Country B}" in rendered
    # PLAN CORRECTION: real macro is \address[N]{}, not \affil[N]{}.
    code_lines = _code_lines(rendered)
    assert not any(line.startswith("\\affil[") for line in code_lines)


# --------------------------------------------------------------------------- #
# Affiliation-index validation (generic MetadataError path, journal-specific
# assertion of the exact culprit/journal-name wording for these three)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("journal_name", ["achemso", "iopart", "wiley"])
def test_out_of_range_affiliation_index_names_journal(journal_name):
    meta = Meta(
        title="T",
        authors=(Author(name="Bob Baker", affiliations=(5,)),),
        affiliations=(Affiliation("Only One Institution"),),
    )
    with pytest.raises(MetadataError) as exc:
        loader.load(journal_name).render_metadata(meta)
    msg = str(exc.value)
    assert "Bob Baker" in msg
    assert journal_name in msg
    assert "5" in msg


# --------------------------------------------------------------------------- #
# achemso: real Tectonic compile through the \ifdefined double-\input trick
# --------------------------------------------------------------------------- #


@requires_tectonic
@skip_without_tectonic
class TestAchemsoCompile:
    """Reproduces the exact file layout emit_project() writes (main.tex +
    generated/preamble.tex + generated/metadata.tex + generated/body.tex +
    generated/bibliography.tex) to prove the \\ifdefined double-\\input trick
    documented in achemso's manifest.yaml/preamble.tex.j2/metadata.tex.j2
    actually compiles achemso's real, class-enforced preamble-only metadata
    macros through this repo's fixed main.tex shape."""

    _MAIN_TEX = (
        "\\input{generated/preamble}\n"
        "\\begin{document}\n"
        "\\input{generated/metadata}\n"
        "\\input{generated/body}\n"
        "\\input{generated/bibliography}\n"
        "\\end{document}\n"
    )
    _BIB = (
        "@article{smith2020test,\n"
        "  author = {Smith, John},\n"
        "  title = {A Test Article},\n"
        "  journal = {Journal of Testing},\n"
        "  year = {2020},\n"
        "}\n"
    )

    def _write_project(self, tmp_path: Path, j) -> Path:
        generated = tmp_path / "generated"
        generated.mkdir()
        (tmp_path / "main.tex").write_text(self._MAIN_TEX, encoding="utf-8")
        (generated / "preamble.tex").write_text(j.render_preamble(), encoding="utf-8")
        (generated / "metadata.tex").write_text(
            j.render_metadata(two_author_meta()), encoding="utf-8"
        )
        (generated / "body.tex").write_text(
            "Hello world, citing \\cite{smith2020test}.\n", encoding="utf-8"
        )
        (generated / "bibliography.tex").write_text(
            "\\bibliography{references}\n", encoding="utf-8"
        )
        (tmp_path / "references.bib").write_text(self._BIB, encoding="utf-8")
        return tmp_path / "main.tex"

    def test_achemso_cls_confirmed_in_tectonic_bundle(self, tmp_path):
        """Negative control / de-risk gate: proves the BUNDLE FINDING directly,
        with a minimal doc and nothing vendored."""
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(
            "\\documentclass[journal=jacsat,manuscript=article]{achemso}\n"
            "\\title{T}\n\\author{A}\n\\affiliation{X}\n"
            "\\begin{document}\n\\maketitle\nHello.\n\\end{document}\n",
            encoding="utf-8",
        )
        result = compile_document(tex_path)
        assert result.success, result.raw_log

    def test_two_author_fixture_compiles_via_double_input_trick(self, tmp_path):
        """The plan's literal done-when for achemso: the two-author fixture
        compiles for real, proving the preamble-only metadata macros land
        before \\begin{document} despite the metadata file being (from
        emit_project()'s perspective) \\input after it."""
        j = loader.load("achemso")
        main_tex = self._write_project(tmp_path, j)

        result = compile_document(main_tex)

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        assert result.pdf_path.stat().st_size > 0
        log_text = result.raw_log
        assert "Can be used only in preamble" not in log_text
        assert "No \\title given" not in log_text

    def test_citation_resolves_through_achemso_bst(self, tmp_path):
        """Real BibTeX pass against the class's own achemso.bst (downloaded
        from the bundle, not vendored) resolves \\cite{smith2020test}.

        Calls tectonic directly with --keep-intermediates (compile_document()
        doesn't pass that flag, so the .bbl it produces is deleted again
        before this test could inspect it) -- same technique
        test_templates_sn_jnl.py uses for its own bst-verification test.
        """
        j = loader.load("achemso")
        main_tex = self._write_project(tmp_path, j)

        tectonic = ensure_tectonic()
        proc = subprocess.run(
            [str(tectonic), "-X", "compile", main_tex.name, "--keep-intermediates", "--keep-logs"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        bbl_path = tmp_path / "main.bbl"
        assert bbl_path.is_file(), "no .bbl written -- citation did not resolve"
        assert "smith2020test" in bbl_path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# iopart: real Tectonic compile through the vendoring path
# --------------------------------------------------------------------------- #


@requires_tectonic
@skip_without_tectonic
def test_iopart_cls_confirmed_absent_from_tectonic_bundle(tmp_path):
    """Negative control: with nothing vendored, iopart.cls must be missing."""
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(
        "\\documentclass[12pt]{iopart}\n\\begin{document}\nHello.\n\\end{document}\n",
        encoding="utf-8",
    )
    result = compile_document(tex_path)
    assert not result.success
    assert any("iopart.cls" in d.message for d in result.errors), result.raw_log


@requires_tectonic
@skip_without_tectonic
class TestIopartVendoredCompile:
    """Real Tectonic compiles exercising templates/journals/iopart/vendor/."""

    _MAIN_TEX = (
        "\\input{preamble}\n"
        "\\begin{document}\n"
        "\\input{metadata}\n"
        "Hello world, citing \\cite{smith2020test}.\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n"
    )
    _BIB = (
        "@article{smith2020test,\n"
        "  author = {Smith, John},\n"
        "  title = {A Test Article},\n"
        "  journal = {Journal of Testing},\n"
        "  year = {2020},\n"
        "}\n"
    )

    def test_two_author_fixture_compiles_via_vendoring(self, tmp_path):
        j = loader.load("iopart")
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(self._MAIN_TEX, encoding="utf-8")
        (tmp_path / "preamble.tex").write_text(j.render_preamble(), encoding="utf-8")
        (tmp_path / "metadata.tex").write_text(
            j.render_metadata(two_author_meta()), encoding="utf-8"
        )
        (tmp_path / "refs.bib").write_text(self._BIB, encoding="utf-8")

        result = compile_document(tex_path, vendor_dir=j.root / "vendor")

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        assert result.pdf_path.stat().st_size > 0
        # Staging actually happened into the compile workdir.
        assert (tmp_path / "iopart.cls").is_file()
        assert (tmp_path / "iopart12.clo").is_file()

    def test_citation_resolves_via_bundled_iopart_num_bst(self, tmp_path):
        """iopart-num.bst is NOT vendored (confirmed IN the Tectonic bundle --
        see manifest.yaml's BUNDLE FINDING); this proves a real BibTeX pass
        resolves the citation using the bundle's copy, with only the class
        vendored."""
        j = loader.load("iopart")
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(self._MAIN_TEX, encoding="utf-8")
        (tmp_path / "preamble.tex").write_text(j.render_preamble(), encoding="utf-8")
        (tmp_path / "metadata.tex").write_text(
            j.render_metadata(two_author_meta()), encoding="utf-8"
        )
        (tmp_path / "refs.bib").write_text(self._BIB, encoding="utf-8")
        stage_vendor_files(j.root / "vendor", tmp_path)
        assert not (tmp_path / "iopart-num.bst").is_file()  # not vendored

        tectonic = ensure_tectonic()
        proc = subprocess.run(
            [str(tectonic), "-X", "compile", "main.tex", "--keep-intermediates", "--keep-logs"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        bbl_path = tmp_path / "main.bbl"
        assert bbl_path.is_file()
        assert "smith2020test" in bbl_path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# wiley: documented-skip path -- no vendoring, no compile test, actionable
# missing-class diagnostic
# --------------------------------------------------------------------------- #


@requires_tectonic
@skip_without_tectonic
def test_wiley_cls_confirmed_absent_from_tectonic_bundle(tmp_path):
    """Negative control proving the BUNDLE FINDING, and that the compile
    wrapper's diagnostic actually names the missing file -- the actionable
    error a user hits if they don't supply their own WileyNJD-v2.cls (see
    manifest.yaml's WHERE TO GET IT note)."""
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(
        "\\documentclass[NoteStyle]{WileyNJD-v2}\n"
        "\\begin{document}\nHello.\n\\end{document}\n",
        encoding="utf-8",
    )
    result = compile_document(tex_path)  # no vendor_dir -- bundle only, as a
    # real user would get if they forgot to supply their own class file.
    assert not result.success
    assert any("WileyNJD-v2.cls" in d.message for d in result.errors), result.raw_log


def test_wiley_compile_is_documented_skip():
    """There is no vendor/ directory and no license basis to obtain one in
    CI, so -- unlike achemso and iopart -- there is no positive compile test
    for wiley in this suite. This test exists so that fact is asserted, not
    silently absent: loading and rendering wiley works fully (proven by the
    golden-file tests above); only compiling the real class does not, by
    design (see manifest.yaml's LICENSE FINDING)."""
    j = loader.load("wiley")
    assert j.vendor == ()
    assert not (j.root / "vendor").exists()
