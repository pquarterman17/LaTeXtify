"""Compile a manuscript through EVERY registered journal and check page-1 layout.

The golden tests assert on rendered LaTeX *source*, which never catches a bug
that only manifests once the document is *compiled* -- e.g. the IEEEtran
metadata template emitting ``\\begin{abstract}`` before ``\\maketitle``, which
compiled fine but pushed the title onto page 2 with the abstract alone on page
1. This sweep emits + compiles a manuscript with a known title and abstract for
each journal and asserts the title actually lands on page 1 (and, when the
abstract shares page 1, that the title precedes it). It is the regression guard
for the whole class of "compiles but lays out wrong" title-block bugs.

Tectonic-marked (needs a real compile); page-1 text is read with pypdf, already
a project dependency, so no extra tooling is required.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from latextify.compile.tectonic import compile_document, ensure_tectonic, find_tectonic
from latextify.emit.project import emit_project
from latextify.templates import loader as templates_loader

FIXTURES = Path(__file__).parent / "fixtures"
BODY_DOCX = FIXTURES / "figures.docx"  # any body-bearing manuscript works

# Unique alnum sentinels: survive LaTeX case/smallcaps transforms and pypdf text
# extraction without colliding with ordinary prose. Matched case-insensitively.
_TITLE_MARK = "Zqxtitle"
_ABSTRACT_MARK = "Zqxabstract"

_PAPER_YAML = f"""\
title: {_TITLE_MARK} Cross-Journal Layout Probe
authors:
- name: Ada Lovelace
  affiliations:
  - 1
  email: ada@example.org
  corresponding: true
- name: Grace Hopper
  affiliations:
  - 2
affiliations:
- Analytical Engine Institute, London, United Kingdom
- Naval Computation Laboratory, New Haven, USA
abstract: {_ABSTRACT_MARK} sentinel abstract body for the cross-journal layout probe,
  long enough to occupy the abstract block on the first page of the compiled document.
keywords:
- layout
- probe
"""


def _tectonic_available() -> bool:
    return find_tectonic() is not None


# Journals whose document class is neither vendored in the template nor present
# in Tectonic's bundled TeX tree, so an offline compile cannot succeed yet. They
# stay IN the sweep (xfail, not skip) so the gap is visible and an eventual
# vendored class turns the xfail into an XPASS that prompts removal from here.
_UNBUNDLED_CLASS = {"wiley"}  # WileyNJD-v2.cls is not bundled


def _journal_params():
    params = []
    for name in sorted(templates_loader.available()):
        marks = ()
        if name in _UNBUNDLED_CLASS:
            marks = pytest.mark.xfail(
                reason="document class not bundled / not in Tectonic bundle; "
                "offline compile unsupported",
                strict=False,
            )
        params.append(pytest.param(name, marks=marks))
    return params


def _first_page_text(pdf_path: Path) -> str:
    from pypdf import PdfReader

    return PdfReader(str(pdf_path)).pages[0].extract_text() or ""


@pytest.mark.tectonic
@pytest.mark.skipif(
    not _tectonic_available(),
    reason="no tectonic binary on PATH/cache and none could be downloaded",
)
@pytest.mark.parametrize("journal", _journal_params())
def test_title_block_lands_on_page_one(journal, tmp_path):
    # Force deterministic metadata via a paper.yaml sidecar beside the body docx.
    docx = tmp_path / "main.docx"
    shutil.copy(BODY_DOCX, docx)
    (tmp_path / "paper.yaml").write_text(_PAPER_YAML, encoding="utf-8")

    result = emit_project(docx, journal, tmp_path / "out")

    journal_obj = templates_loader.load(journal)
    vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
    compiled = compile_document(
        result.main_tex_path, tectonic_path=ensure_tectonic(), vendor_dir=vendor_dir
    )
    assert compiled.success, f"{journal}: manuscript failed to compile"

    page1 = _first_page_text(compiled.pdf_path).lower()
    assert _TITLE_MARK.lower() in page1, (
        f"{journal}: title is missing from page 1 -- the title block did not render "
        "on the first page (the IEEEtran abstract-before-maketitle failure mode)."
    )
    # When the abstract also renders on page 1, the title must come first.
    if _ABSTRACT_MARK.lower() in page1:
        assert page1.index(_TITLE_MARK.lower()) < page1.index(_ABSTRACT_MARK.lower()), (
            f"{journal}: the abstract precedes the title on page 1 -- the title block "
            "is being pushed down/after the abstract."
        )
