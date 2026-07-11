"""End-to-end EndNote citation test THROUGH COMPILE (plan item 13).

Mirrors test_citations_compile_stub.py's pattern for the Zotero/Mendeley
fixture (plan items 7 + 24): the sentinel planter and emitter are source-
agnostic (they operate on Citation records, not on which extractor produced
them), so the same ``ZZLTXCITE<i>ZZ`` -> ``\\cite{key,...}`` linkage proven
there for Zotero/Mendeley is exercised here for EndNote -- one tectonic-
marked through-compile test is enough per plan item 13's done-when; the
Word-native extractor is covered by test_citations_extract_wordnative.py's
extraction + body-linkage checks (via test_citations_degradation.py's
emit_project use) without a second real compile.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from latextify.compile.tectonic import TectonicNotAvailableError, ensure_tectonic
from latextify.emit.project import emit_project

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "endnote_cited.docx"

EXPECTED_KEYS = (
    "feynman1969quantum",
    "pathria1972statistical",
    "turing1950scalable",
    "wilczek1982topological",
    "devoret2013superconducting",
)

EXPECTED_CITES = (
    "\\cite{feynman1969quantum}",
    "\\cite{pathria1972statistical,turing1950scalable}",
    "\\cite{wilczek1982topological}",
    "\\cite{devoret2013superconducting}",
)


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_endnote_cited", FIXTURE_DIR / "make_endnote_cited.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.build()


def _tectonic_available() -> bool:
    try:
        ensure_tectonic()
        return True
    except TectonicNotAvailableError:
        return False


pytestmark = [
    pytest.mark.tectonic,
    pytest.mark.skipif(
        not _tectonic_available(),
        reason="no tectonic binary on PATH/cache and none could be downloaded",
    ),
]


def test_endnote_cited_links_cites_and_compiles(tmp_path):
    _ensure_fixture()
    docx = tmp_path / DOCX.name
    shutil.copy(DOCX, docx)

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    bib_text = result.bib_path.read_text(encoding="utf-8")
    for key in EXPECTED_KEYS:
        assert f"{{{key}," in bib_text
    assert result.citation_count == 4

    body = result.body_tex_path.read_text(encoding="utf-8")
    for cite in EXPECTED_CITES:
        assert cite in body, f"missing {cite} in body.tex:\n{body}"
    positions = [body.index(cite) for cite in EXPECTED_CITES]
    assert positions == sorted(positions)
    assert "ZZLTXCITE" not in body
    assert "%%" not in body
    assert not any("linked into the body" in w.message for w in result.warnings)

    tectonic = ensure_tectonic()
    main = result.main_tex_path
    proc = subprocess.run(
        [str(tectonic), "-X", "compile", main.name, "--keep-intermediates", "--keep-logs"],
        cwd=str(main.parent),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    pdf_path = main.parent / f"{main.stem}.pdf"
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 0

    bbl_path = main.parent / f"{main.stem}.bbl"
    assert bbl_path.is_file(), "tectonic did not emit a .bbl to verify citation resolution"
    bbl_text = bbl_path.read_text(encoding="utf-8", errors="replace")
    for key in EXPECTED_KEYS:
        assert key in bbl_text, f"{key} missing from the compiled bibliography (.bbl)"

    log_text = (main.parent / f"{main.stem}.log").read_text(encoding="utf-8", errors="replace")
    assert "Citation" not in log_text or "undefined" not in log_text.lower()
