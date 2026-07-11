"""End-to-end citation test THROUGH COMPILE.

Plan item 7's final sub-task ("End-to-end fixture test through compile")
needed items 3 (pandoc body pipeline), 5 (project emitter), and 6 (Tectonic
compile wrapper) to land. All three are now merged; this activates it.

IMPORTANT deviation from the plan's literal done-when text, discovered while
implementing item 5 -- reported rather than worked around per the executor
protocol: pandoc's docx reader (pypandoc-binary's bundled pandoc 3.9) does
NOT recognize either the Zotero ("ADDIN ZOTERO_ITEM CSL_CITATION {json}") or
Mendeley ("ADDIN CSL_CITATION {json}") field-code instructions as native
``Cite`` AST elements -- verified empirically against zotero_cited.docx (and
against a from-scratch minimal fldSimple-encoded field, to rule out the
hand-crafted fixture's complex-field encoding as the cause): pandoc's JSON
AST contains plain ``Str`` runs of the field's cached display text ("[1]",
"[2, 3]", ...), never a ``Cite`` node. `latextify.ingest.filters.plant_anchors`
only plants a ``%%CITE:<idx>%%`` anchor when pandoc hands it a ``Cite`` node,
so for this citation source **no anchors are planted in the body at all**.
The bibliography extraction (`latextify.citations.fields`, item 7) is
independent of pandoc and unaffected -- every reference still lands in
`references.bib` correctly keyed. The gap is body-side inline linkage only,
and it is upstream of item 5 (item 3's territory); item 5's emitter surfaces
it as an `EmitWarning` rather than fabricating `\\cite{}` commands that were
never anchored. See `latextify/emit/project.py`'s `_citation_linkage_warning`.

This test therefore asserts what is actually true end-to-end: the project
compiles, every extracted reference's key is present in `references.bib`
inside the compiled tree, and the linkage-gap warning fires -- rather than
asserting `\\cite{muller2020quantum}` appears in the body, which is not
achievable without changes to item 3 (out of scope for the emitter).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from latextify.compile.tectonic import (
    TectonicNotAvailableError,
    compile_document,
    ensure_tectonic,
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


def test_zotero_cited_compiles_with_bib_entries_present(tmp_path):
    _ensure_fixture()
    # Emit from a tmp copy: load_or_create_meta writes a write-once
    # paper.yaml beside the docx path it's given, which must not land in
    # the committed fixtures directory.
    docx = tmp_path / DOCX.name
    shutil.copy(DOCX, docx)

    result = emit_project(docx, "revtex4-2", tmp_path / "output")

    # Bibliography extraction is independent of pandoc's anchor recognition
    # gap (see module docstring) -- every reference must still be present.
    bib_text = result.bib_path.read_text(encoding="utf-8")
    for key in EXPECTED_KEYS:
        assert f"{{{key}," in bib_text
    # 4 in-text Citation records (one field has two citationItems), 5 bib entries.
    assert result.citation_count == 4

    # No unresolved anchors leak into the generated body (there were none to
    # resolve, but this also guards against a regression that stops swallowing them).
    assert "%%" not in result.body_tex_path.read_text(encoding="utf-8")

    # The verified pandoc gap: citations extracted, but not linked inline.
    assert any("linked into the body" in w.message for w in result.warnings)

    compile_result = compile_document(result.main_tex_path)
    assert compile_result.success, compile_result.raw_log
    assert compile_result.pdf_path is not None
    assert compile_result.pdf_path.is_file()
    assert compile_result.pdf_path.stat().st_size > 0
