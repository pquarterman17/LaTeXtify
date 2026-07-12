"""End-to-end "[N]"-prefixed reference list reconstruction on bracket_cited.docx (GAP 1).

Real papers frequently type reference lists as "[1] A. Author, Title, Journal
12, 345 (2020)." rather than "1. A. Author, ...". Before the fix,
``segment_reference_list`` recognized typed "1."-style digits and Word
auto-numbering (``w:numPr``) but not a leading bracketed "[N]" -- every entry
got ``ref_number=None``, in-text numeric markers could never link, and the raw
"[N]" leaked into the reconstructed entry text (poisoning both the Crossref
query and the generated BibTeX key, e.g. an observed key "4b2015" with title
"{[}4]...").

Crossref is mocked with ``httpx.MockTransport``; every one of the 5 typed
references matches a canned record so reconciliation succeeds cleanly (the
confidence-scoring edge cases already have their own coverage in
``test_citations_reconstruct.py``/``hand_cited.docx`` -- this fixture is
specifically about the bracket-prefix parsing path). The final tests drive the
whole emitter and assert the ``[2]``/``[3,5]``/``[1-3]`` markers land as
``\\cite{}`` in the generated body with no leaked bracket text anywhere.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import httpx
import pytest

from latextify.citations.crossref import CrossrefClient
from latextify.citations.plaintext import reconstruct_citations
from latextify.emit.project import emit_project

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCX = FIXTURE_DIR / "bracket_cited.docx"

# Distinctive lower-case phrase in each reference -> its canned Crossref record.
_MOCK: dict[str, dict] = {
    "foundational widget calibration": {
        "title": ["Foundational widget calibration techniques"],
        "author": [{"family": "Alpha", "given": "A.", "sequence": "first"}],
        "issued": {"date-parts": [[2020]]},
        "DOI": "10.1000/widget.0001",
        "type": "journal-article",
        "container-title": ["Journal of Widget Physics"],
    },
    "superconducting widget arrays": {
        "title": ["Superconducting widget arrays at low temperature"],
        "author": [{"family": "Bravo", "given": "B.", "sequence": "first"}],
        "issued": {"date-parts": [[2019]]},
        "DOI": "10.1000/widget.0002",
        "type": "journal-article",
        "container-title": ["Journal of Widget Physics"],
    },
    "magnetotransport in doped widget": {
        "title": ["Magnetotransport in doped widget thin films"],
        "author": [{"family": "Charlie", "given": "C.", "sequence": "first"}],
        "issued": {"date-parts": [[2018]]},
        "DOI": "10.1000/widget.0003",
        "type": "journal-article",
        "container-title": ["Journal of Widget Physics"],
    },
    "widget growth via molecular beam epitaxy": {
        "title": ["Widget growth via molecular beam epitaxy"],
        "author": [{"family": "Delta", "given": "D.", "sequence": "first"}],
        "issued": {"date-parts": [[2017]]},
        "DOI": "10.1000/widget.0004",
        "type": "journal-article",
        "container-title": ["Journal of Widget Physics"],
    },
    "spectroscopic signatures of widget defects": {
        "title": ["Spectroscopic signatures of widget defects"],
        "author": [{"family": "Echo", "given": "E.", "sequence": "first"}],
        "issued": {"date-parts": [[2016]]},
        "DOI": "10.1000/widget.0005",
        "type": "journal-article",
        "container-title": ["Journal of Widget Physics"],
    },
}

_EXPECTED_DOIS = {
    1: "10.1000/widget.0001",
    2: "10.1000/widget.0002",
    3: "10.1000/widget.0003",
    4: "10.1000/widget.0004",
    5: "10.1000/widget.0005",
}


def _handler(request: httpx.Request) -> httpx.Response:
    query = request.url.params.get("query.bibliographic", "").lower()
    for keyword, item in _MOCK.items():
        if keyword in query:
            return httpx.Response(200, json={"message": {"items": [item]}})
    return httpx.Response(200, json={"message": {"items": []}})


def _mock_client() -> CrossrefClient:
    return CrossrefClient(mailto="test@example.com", transport=httpx.MockTransport(_handler))


def _ensure_fixture() -> None:
    if DOCX.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "make_bracket_cited", FIXTURE_DIR / "make_bracket_cited.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.build()


@pytest.fixture(scope="module")
def result():
    _ensure_fixture()
    with _mock_client() as client:
        return reconstruct_citations(DOCX, client=client)


# --------------------------------------------------------------------------- #
# reconstruction quality: bracket prefixes parsed into ref_number, stripped
# from the text handed to Crossref/raw-entry emission
# --------------------------------------------------------------------------- #


def test_reference_list_found(result):
    assert result.has_reference_list
    assert len(result.entries) == 5
    assert result.report.total == 5


def test_all_five_bracket_prefixed_references_reconstructed_with_dois(result):
    assert result.report.matched_fraction == 1.0
    doi_by_number = {r.ref_number: r.doi for r in result.records}
    for number, expected in _EXPECTED_DOIS.items():
        assert doi_by_number[number] == expected, number


def test_numeric_positions_map_to_keys(result):
    # Every bracket-prefixed entry resolves to ref_number 1..5, including
    # entry 4 (typed with no space after "]") -- the specific shape that used
    # to leave ref_number=None.
    assert set(result.keys_by_number) == {1, 2, 3, 4, 5}
    assert all(result.keys_by_number.values())


def test_no_leaked_bracket_in_reconstructed_entries(result):
    # The raw "[N]" prefix must never survive into any entry's title/key --
    # the exact real-world bug (observed key "4b2015" with title "{[}4]...").
    for entry in result.entries:
        assert "[" not in (entry.title or "")
        assert "]" not in (entry.title or "")
        assert entry.key and not entry.key[0].isdigit()


# --------------------------------------------------------------------------- #
# whole-emitter integration: [2] / [3,5] / [1-3] markers become \cite{} in
# the generated body
# --------------------------------------------------------------------------- #


def reconstruct_with_mock(docx: Path):
    with _mock_client() as client:
        return reconstruct_citations(docx, client=client)


def test_emit_project_links_bracket_prefixed_markers(tmp_path, monkeypatch):
    _ensure_fixture()
    docx = tmp_path / "bracket_cited.docx"
    shutil.copy(DOCX, docx)

    # reconstruct_citations builds `crossref.CrossrefClient(...)`; redirect that
    # construction to a mock-transport-backed client so no network is touched.
    monkeypatch.setattr(
        "latextify.citations.crossref.CrossrefClient",
        lambda **kwargs: _mock_client(),
    )

    emit_result = emit_project(docx, "revtex4-2", tmp_path / "output")
    body = emit_result.body_tex_path.read_text(encoding="utf-8")
    bib = emit_result.bib_path.read_text(encoding="utf-8")

    keys = {r.ref_number: r.key for r in reconstruct_with_mock(docx).records}

    # [2] single, [3,5] list, [1-3] range -- all linked correctly.
    assert f"\\cite{{{keys[2]}}}" in body
    assert f"\\cite{{{keys[3]},{keys[5]}}}" in body
    assert f"\\cite{{{keys[1]},{keys[2]},{keys[3]}}}" in body

    # No raw bracket markers survive, and the typed reference list is gone.
    assert "{[}" not in body
    assert "\\section{References}" not in body
    assert "Spectroscopic signatures" not in body

    # Every reconstructed entry is in the .bib, keyed cleanly (no leaked "[").
    for key in keys.values():
        assert f"{{{key}," in bib
    for line in bib.splitlines():
        if line.strip().startswith("title"):
            assert "[" not in line and "]" not in line

    assert emit_result.citation_count == 3  # three distinct in-text markers linked


def test_emit_project_raises_no_reconciliation_warnings(tmp_path, monkeypatch):
    # All 5 references matched cleanly -- no "could not be confidently
    # matched" verify-warnings, unlike hand_cited.docx's deliberately obscure
    # entry 7.
    _ensure_fixture()
    docx = tmp_path / "bracket_cited.docx"
    shutil.copy(DOCX, docx)
    monkeypatch.setattr(
        "latextify.citations.crossref.CrossrefClient",
        lambda **kwargs: _mock_client(),
    )

    emit_result = emit_project(docx, "revtex4-2", tmp_path / "output")
    assert not any("could not be confidently matched" in w.message for w in emit_result.warnings)
    assert not any("unresolved" in w.message.lower() for w in emit_result.warnings)
