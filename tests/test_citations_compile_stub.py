"""End-to-end citation test THROUGH COMPILE (plan items 7 + 24).

Item 7's final sub-task ("End-to-end fixture test through compile") needed
items 3 (pandoc body pipeline), 5 (project emitter), and 6 (Tectonic compile
wrapper). Its original done-when -- actual ``\\cite{...}`` in the body and a
PDF that resolves them against ``references.bib`` -- was blocked by a verified
pandoc gap (item 5's finding): pandoc 3.9's docx reader never turns
Zotero/Mendeley citation field codes into native ``Cite`` AST nodes, so no
``%%CITE`` anchor was ever planted and no ``\\cite`` reached the body.

Item 24 closed that gap by preprocessing: before pandoc runs,
``latextify.ingest.citation_sentinels`` rewrites each citation field's
displayed result to an alphanumeric ``ZZLTXCITE<i>ZZ`` sentinel (pandoc's LaTeX
writer escapes ``%`` -> ``\\%``, so a ``%%CITE``-style sentinel would be
mangled; alphanumeric text survives verbatim). The sentinel index is 0-based in
the SAME document-order field walk ``extract_field_citations`` uses, so it pairs
with the ``Citation`` whose ``.index`` is that number -- including a citation
field nested inside a ``PAGEREF``. The emitter swaps each sentinel for
``\\cite{key,...}``.

So this test now asserts the original done-when directly: the four in-text
``\\cite{}`` commands are present in ``generated/body.tex`` (with the correct
keys and the multi-item and nested cases handled), the document compiles, the
BibTeX-produced ``.bbl`` carries every reference key (so the PDF's bibliography
resolves against ``references.bib``), and the "citations extracted but not
linked" ``EmitWarning`` no longer fires.

NOTE (plan assumption corrected): Tectonic's ``-X compile`` does NOT retain the
intermediate ``.bbl`` on disk by default -- only ``.blg``/``.log``/``.pdf``. The
``.bbl`` assertion therefore runs the compile with ``--keep-intermediates`` (a
real end-to-end compile via ``ensure_tectonic()``, the same binary
``compile_document`` uses).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from latextify.compile.tectonic import (
    ensure_tectonic,
    find_tectonic,
)
from latextify.emit.project import emit_project

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "zotero_cited.docx"

EXPECTED_KEYS = (
    "muller2020quantum",
    "kittel2005introduction",
    "smith2019scalable",
    "garcia2018topological",
    "smith2021superconductivity",
)

# Each in-text citation and the \cite{} it must resolve to (document order):
#   0 article    -> single key
#   1 multi      -> two keys, in citationItems order
#   2 chapter    -> single key, nested inside a PAGEREF field
#   3 mendeley   -> single key
EXPECTED_CITES = (
    "\\cite{muller2020quantum}",
    "\\cite{kittel2005introduction,smith2019scalable}",
    "\\cite{garcia2018topological}",
    "\\cite{smith2021superconductivity}",
)


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_zotero_cited", FIXTURE_DIR / "make_zotero_cited.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.build()


def _tectonic_available() -> bool:
    # Detection only -- must NOT download at collection time: anonymous
    # GitHub API calls from CI runners hit rate limits, and unit jobs
    # deselect tectonic tests anyway. ensure_tectonic() still runs (and
    # downloads if needed) inside the marked tests themselves; CI's
    # integration job pre-fetches the binary before pytest.
    return find_tectonic() is not None


pytestmark = [
    pytest.mark.tectonic,
    pytest.mark.skipif(
        not _tectonic_available(),
        reason="no tectonic binary on PATH/cache and none could be downloaded",
    ),
]


def test_zotero_cited_links_cites_and_compiles(tmp_path):
    _ensure_fixture()
    # Emit from a tmp copy: load_or_create_meta writes a write-once paper.yaml
    # beside the docx, which must not land in the committed fixtures directory.
    docx = tmp_path / DOCX.name
    shutil.copy(DOCX, docx)

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    # Bibliography is complete (extraction is independent of the pandoc gap).
    bib_text = result.bib_path.read_text(encoding="utf-8")
    for key in EXPECTED_KEYS:
        assert f"{{{key}," in bib_text
    # 4 in-text Citation records (one field has two citationItems), 5 bib entries.
    assert result.citation_count == 4

    # Original item 7 done-when: real \cite{} commands in the body, including the
    # multi-item field and the citation nested inside a PAGEREF (index 2).
    body = result.body_tex_path.read_text(encoding="utf-8")
    for cite in EXPECTED_CITES:
        assert cite in body, f"missing {cite} in body.tex:\n{body}"
    # Document order is preserved.
    positions = [body.index(cite) for cite in EXPECTED_CITES]
    assert positions == sorted(positions)
    # Every sentinel resolved; no unresolved anchors leak through either.
    assert "ZZLTXCITE" not in body
    assert "%%" not in body

    # The verified pandoc gap is now closed: the linkage warning must NOT fire.
    assert not any("linked into the body" in w.message for w in result.warnings)

    # Compile and prove the cites resolve against references.bib: keep the
    # BibTeX intermediate so we can read the generated .bbl.
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

    # No unresolved \cite: the compile log must carry no undefined-citation warning.
    log_text = (main.parent / f"{main.stem}.log").read_text(encoding="utf-8", errors="replace")
    assert "Citation" not in log_text or "undefined" not in log_text.lower()
