"""Tests for the Springer Nature (sn-jnl) journal template (plan item 12).

A separate file from ``test_templates.py`` on purpose: items 10 (elsarticle)
and 11 (IEEEtran) add their own journal folders in parallel and may touch
that shared file, so sn-jnl's tests -- including the real-compile vendoring
test -- live here to avoid collisions.

sn-jnl is the first journal in this repo genuinely absent from Tectonic's
bundle (verified below and in the manifest's BUNDLE FINDING comment), so
``TestVendoredCompile`` is the point of this file: it proves the
``templates/journals/sn-jnl/vendor/`` files actually make an otherwise-
unresolvable ``\\documentclass{sn-jnl}`` compile, through both declared
citation modes and through a real BibTeX pass against the vendored
``sn-mathphys-{num,ay}.bst`` styles.

LICENSE FINDING (see manifest.yaml + VENDOR_LICENSE.txt for the full
writeup): sn-jnl.cls and every sn-*.bst file carry their own LaTeX Project
Public License (LPPL) header, verified against the official kit downloaded
directly from Springer Nature's own LaTeX author-support page -- LPPL
permits unmodified redistribution, so the three files this journal needs
are committed verbatim rather than fetched via a vendor_fetch mechanism.
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
from latextify.templates.authors import format_affil_refs
from latextify.templates.loader import ManifestError

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def two_author_meta() -> Meta:
    """Two authors, two affiliations, one corresponding author spanning both.

    Mirrors ``test_templates.py``'s ``two_author_meta`` fixture so the two
    journals are directly comparable, but this is the case that specifically
    proves the ``\\author*[...]`` corresponding-author path: Alice is
    corresponding (spans both affiliations), Bob is not (single affiliation).
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


def test_load_sn_jnl_returns_validated_journal():
    j = loader.load("sn-jnl")
    assert j.name == "sn-jnl"
    assert j.document_class == "sn-jnl"
    assert j.class_options == ("pdflatex",)
    assert [p.name for p in j.packages] == ["amsmath", "amssymb", "graphicx", "bm", "booktabs"]
    assert j.default_mode == "numeric"
    assert j.bib_modes["numeric"].bibstyle == "sn-mathphys-num"
    assert j.bib_modes["authoryear"].bibstyle == "sn-mathphys-ay"
    assert j.metadata_scheme == "sn-jnl"
    assert j.figure_env.single == "figure"
    assert j.figure_env.wide == "figure*"


def test_sn_jnl_vendor_lists_exactly_the_declared_bib_modes():
    j = loader.load("sn-jnl")
    assert set(j.vendor) == {"sn-jnl.cls", "sn-mathphys-num.bst", "sn-mathphys-ay.bst"}


def test_available_lists_sn_jnl():
    assert "sn-jnl" in loader.available()


def test_hyperref_not_in_packages_list():
    """sn-jnl force-loads hyperref itself -- see manifest.yaml's comment.

    It must not appear in the declared ``packages`` list (that would emit a
    second, differently-optioned ``\\usepackage{hyperref}`` after the
    class's own bare load and risk an option clash).
    """
    j = loader.load("sn-jnl")
    assert "hyperref" not in [p.name for p in j.packages]


# --------------------------------------------------------------------------- #
# Author-reference formatting helper (plan item 12's grouping-free scheme)
# --------------------------------------------------------------------------- #


def test_format_affil_refs_converts_0_based_to_1_based_csv():
    assert format_affil_refs((0, 1)) == "1,2"
    assert format_affil_refs((2,)) == "3"
    assert format_affil_refs(()) == ""


# --------------------------------------------------------------------------- #
# Golden-file rendering
# --------------------------------------------------------------------------- #


def test_rendered_preamble_matches_golden():
    j = loader.load("sn-jnl")
    expected = (GOLDEN / "sn-jnl_preamble.tex").read_text(encoding="utf-8")
    assert j.render_preamble() == expected


def test_rendered_preamble_authoryear_matches_golden():
    """Proves the class-option-switching mechanism (see manifest.yaml's NOTE):

    switching citation mode must change the injected class option from
    ``sn-mathphys-num`` to ``sn-mathphys-ay`` -- not just the bibstyle line.
    """
    j = loader.load("sn-jnl")
    expected = (GOLDEN / "sn-jnl_preamble_authoryear.tex").read_text(encoding="utf-8")
    rendered = j.render_preamble(mode="authoryear")
    assert rendered == expected
    assert "\\documentclass[pdflatex,sn-mathphys-ay]{sn-jnl}" in rendered
    assert "sn-mathphys-num" not in rendered


def test_rendered_metadata_matches_golden():
    j = loader.load("sn-jnl")
    expected = (GOLDEN / "sn-jnl_metadata.tex").read_text(encoding="utf-8")
    assert j.render_metadata(two_author_meta()) == expected


def test_metadata_uses_starred_author_for_corresponding_only():
    """Direct assertion of the plan's done-when: \\author* vs plain \\author."""
    j = loader.load("sn-jnl")
    rendered = j.render_metadata(two_author_meta())
    assert "\\author*[1,2]{Alice Anderson}" in rendered  # corresponding
    assert "\\author[2]{Bob Baker}" in rendered  # not corresponding
    assert "\\author[2]{Alice Anderson}" not in rendered
    assert "\\author*[2]{Bob Baker}" not in rendered
    assert "\\affil[1]{Department of Physics, University A, City A, Country A}" in rendered
    assert "\\affil[2]{National High Field Laboratory, City B, Country B}" in rendered


def test_unsupported_citation_mode_lists_allowed_modes():
    j = loader.load("sn-jnl")
    with pytest.raises(ManifestError) as exc:
        j.render_preamble(mode="not-a-real-mode")
    msg = str(exc.value)
    assert "not-a-real-mode" in msg
    assert "numeric" in msg
    assert "authoryear" in msg
    assert "sn-jnl" in msg


# --------------------------------------------------------------------------- #
# Real compile through the vendoring path (plan item 12's core done-when)
# --------------------------------------------------------------------------- #

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


def _tectonic_available() -> bool:
    try:
        ensure_tectonic()
        return True
    except TectonicNotAvailableError:
        return False


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
def test_sn_jnl_cls_confirmed_absent_from_tectonic_bundle(tmp_path):
    """The plan's own risk note: sn-jnl 'almost certainly' isn't bundled.

    Verify that directly, with nothing vendored -- this is the negative
    control for ``TestVendoredCompile`` below and confirms the vendoring
    path is load-bearing for this journal, not a fallback that never fires
    (unlike REVTeX, item 6, which turned out to be in the bundle).
    """
    tex_path = tmp_path / "main.tex"
    tex_path.write_text(
        "\\documentclass[pdflatex,sn-mathphys-num]{sn-jnl}\n"
        "\\begin{document}\nHello.\n\\end{document}\n",
        encoding="utf-8",
    )

    result = compile_document(tex_path)  # no vendor_dir -- bundle only

    assert not result.success
    assert any("sn-jnl.cls" in d.message for d in result.errors), result.raw_log


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
class TestVendoredCompile:
    """Real Tectonic compiles exercising latextify/templates/journals/sn-jnl/vendor/."""

    def test_two_author_fixture_compiles_via_vendoring(self, tmp_path):
        """The plan's literal done-when: the two-author fixture compiles
        via the vendoring path specifically (vendor_dir passed explicitly,
        no reliance on the Tectonic bundle)."""
        j = loader.load("sn-jnl")
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(_MAIN_TEX, encoding="utf-8")
        (tmp_path / "preamble.tex").write_text(j.render_preamble(), encoding="utf-8")
        (tmp_path / "metadata.tex").write_text(
            j.render_metadata(two_author_meta()), encoding="utf-8"
        )
        (tmp_path / "refs.bib").write_text(_BIB, encoding="utf-8")

        result = compile_document(tex_path, vendor_dir=j.root / "vendor")

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        assert result.pdf_path.stat().st_size > 0
        # staging actually happened into the compile workdir
        assert (tmp_path / "sn-jnl.cls").is_file()
        assert (tmp_path / "sn-mathphys-num.bst").is_file()

    @pytest.mark.parametrize(
        "mode, bibstyle",
        [("numeric", "sn-mathphys-num"), ("authoryear", "sn-mathphys-ay")],
    )
    def test_each_declared_bib_mode_resolves_citations_via_vendored_bst(
        self, tmp_path, mode, bibstyle
    ):
        """Exercise the vendored .bst files themselves (not just the .cls):
        a real BibTeX pass must resolve \\cite{smith2020test} against
        refs.bib using the vendored sn-mathphys-{num,ay}.bst style."""
        j = loader.load("sn-jnl")
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(_MAIN_TEX, encoding="utf-8")
        (tmp_path / "preamble.tex").write_text(j.render_preamble(mode=mode), encoding="utf-8")
        (tmp_path / "metadata.tex").write_text(
            j.render_metadata(two_author_meta()), encoding="utf-8"
        )
        (tmp_path / "refs.bib").write_text(_BIB, encoding="utf-8")
        stage_vendor_files(j.root / "vendor", tmp_path)

        tectonic = ensure_tectonic()
        proc = subprocess.run(
            [str(tectonic), "-X", "compile", "main.tex", "--keep-intermediates", "--keep-logs"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

        pdf_path = tmp_path / "main.pdf"
        assert pdf_path.is_file()
        assert pdf_path.stat().st_size > 0

        bbl_path = tmp_path / "main.bbl"
        assert bbl_path.is_file(), "tectonic did not emit a .bbl to verify citation resolution"
        bbl_text = bbl_path.read_text(encoding="utf-8", errors="replace")
        assert "smith2020test" in bbl_text

        log_text = (tmp_path / "main.log").read_text(encoding="utf-8", errors="replace")
        assert "undefined" not in log_text.lower() or "Citation" not in log_text
