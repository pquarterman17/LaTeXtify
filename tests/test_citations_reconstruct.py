"""End-to-end plain-text reconstruction on hand_cited.docx (plan item 14).

Crossref is mocked with ``httpx.MockTransport``; the mock returns a full,
correct record for 11 of the 12 typed references and nothing for the obscure
7th, so the fixture reconstructs > 80% with correct DOIs while reference 7 is
flagged. The final test drives the whole emitter and asserts the linkable
markers land as ``\\cite{}`` in the generated body.
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
DOCX = FIXTURE_DIR / "hand_cited.docx"

# Distinctive lower-case phrase in each reference -> its canned Crossref record.
# No entry for reference 7 ("cryogenic amplifier noise") -> flagged for verify.
_MOCK: dict[str, dict] = {
    "coherent control": {
        "title": ["Coherent control of solid-state spin qubits"],
        "author": [{"family": "Smith", "given": "A. B.", "sequence": "first"}],
        "issued": {"date-parts": [[2020]]},
        "DOI": "10.1038/s41567-020-00001",
        "type": "journal-article",
        "container-title": ["Nature Physics"],
    },
    "topological superconductivity": {
        "title": ["Observation of topological superconductivity in a planar Josephson junction"],
        "author": [{"family": "Anderson", "given": "P. W.", "sequence": "first"}],
        "issued": {"date-parts": [[2015]]},
        "DOI": "10.1103/PhysRevLett.115.020501",
        "type": "journal-article",
    },
    "weyl semimetals": {
        "title": ["Magnetotransport signatures of Weyl semimetals"],
        "author": [{"family": "Brown", "given": "R. J.", "sequence": "first"}],
        "issued": {"date-parts": [[2018]]},
        "DOI": "10.1103/PhysRevB.98.035001",
        "type": "journal-article",
    },
    "two-dimensional magnets": {
        "title": ["Two-dimensional magnets and their van der Waals heterostructures"],
        "author": [{"family": "Chen", "given": "X.", "sequence": "first"}],
        "issued": {"date-parts": [[2019]]},
        "DOI": "10.1038/s41563-019-00004",
        "type": "journal-article",
    },
    "quantum oscillations": {
        "title": ["Quantum oscillations in underdoped high-Tc cuprates"],
        "author": [{"family": "Davis", "given": "M. K.", "sequence": "first"}],
        "issued": {"date-parts": [[2017]]},
        "DOI": "10.1126/science.aan0005",
        "type": "journal-article",
    },
    "moire flat bands": {
        "title": ["Moire flat bands in magic-angle twisted bilayer graphene"],
        "author": [{"family": "Evans", "given": "L.", "sequence": "first"}],
        "issued": {"date-parts": [[2021]]},
        "DOI": "10.1103/RevModPhys.93.025006",
        "type": "journal-article",
    },
    "spin-orbit torque": {
        "title": ["Spin-orbit torque switching of perpendicular magnetization"],
        "author": [{"family": "Garcia", "given": "H.", "sequence": "first"}],
        "issued": {"date-parts": [[2014]]},
        "DOI": "10.1038/nnano.2014.00008",
        "type": "journal-article",
    },
    "majorana zero modes": {
        "title": ["Majorana zero modes in semiconductor nanowires"],
        "author": [{"family": "Hughes", "given": "T. L.", "sequence": "first"}],
        "issued": {"date-parts": [[2013]]},
        "DOI": "10.1126/science.12300009",
        "type": "journal-article",
    },
    "room-temperature superconductivity": {
        "title": ["Room-temperature superconductivity in a hydride under high pressure"],
        "author": [{"family": "Ito", "given": "K.", "sequence": "first"}],
        "issued": {"date-parts": [[2022]]},
        "DOI": "10.1038/s41586-022-000010",
        "type": "journal-article",
    },
    "berry-phase effects": {
        "title": ["Berry-phase effects on electronic transport properties"],
        "author": [{"family": "Johnson", "given": "D. R.", "sequence": "first"}],
        "issued": {"date-parts": [[2012]]},
        "DOI": "10.1103/RevModPhys.84.001100",
        "type": "journal-article",
    },
    "anomalous hall effect": {
        "title": ["Anomalous Hall effect in itinerant ferromagnets"],
        "author": [{"family": "Novak", "given": "V.", "sequence": "first"}],
        "issued": {"date-parts": [[2011]]},
        "DOI": "10.1103/RevModPhys.83.001500",
        "type": "journal-article",
    },
}

# Reference number -> expected DOI (7 intentionally absent).
_EXPECTED_DOIS = {
    1: "10.1038/s41567-020-00001",
    2: "10.1103/PhysRevLett.115.020501",
    3: "10.1103/PhysRevB.98.035001",
    4: "10.1038/s41563-019-00004",
    5: "10.1126/science.aan0005",
    6: "10.1103/RevModPhys.93.025006",
    8: "10.1038/nnano.2014.00008",
    9: "10.1126/science.12300009",
    10: "10.1038/s41586-022-000010",
    11: "10.1103/RevModPhys.84.001100",
    12: "10.1103/RevModPhys.83.001500",
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
        "make_hand_cited", FIXTURE_DIR / "make_hand_cited.py"
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
# reconstruction quality
# --------------------------------------------------------------------------- #


def test_reference_list_found(result):
    assert result.has_reference_list
    assert len(result.entries) == 12
    assert result.report.total == 12


def test_at_least_80_percent_reconstructed_with_dois(result):
    matched_with_doi = [r for r in result.records if r.matched and r.doi]
    assert len(matched_with_doi) >= 10  # 11/12 = 91% >= 80%
    assert result.report.matched_fraction >= 0.8


def test_matched_references_have_correct_dois(result):
    doi_by_number = {r.ref_number: r.doi for r in result.records}
    for number, expected in _EXPECTED_DOIS.items():
        assert doi_by_number[number] == expected, number


def test_obscure_reference_is_flagged(result):
    flagged = [r for r in result.records if r.verify]
    assert result.report.flagged_count == 1
    assert len(flagged) == 1
    assert flagged[0].ref_number == 7
    assert flagged[0].source == "raw"
    assert flagged[0].doi is None


def test_numeric_positions_map_to_keys(result):
    # Every reference position resolves to a key (for numeric-marker linkage).
    assert set(result.keys_by_number) == set(range(1, 13))
    assert all(result.keys_by_number.values())


def test_author_year_index_contains_smith_2020(result):
    key = result.keys_by_number[1]
    assert ("smith", "2020") in result.author_year_keys
    assert key in result.author_year_keys[("smith", "2020")]


# --------------------------------------------------------------------------- #
# whole-emitter integration: markers become \cite{} in the generated body
# --------------------------------------------------------------------------- #


def test_emit_project_links_plaintext_markers(tmp_path, monkeypatch):
    _ensure_fixture()
    docx = tmp_path / "hand_cited.docx"
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

    # Numeric single, range, superscript, and author-year markers all linked.
    assert f"\\cite{{{keys[1]}}}" in body  # [1] and (Smith et al., 2020)
    assert f"\\cite{{{keys[12]}}}" in body  # [12]
    assert f"\\cite{{{keys[3]},{keys[4]},{keys[5]},{keys[8]}}}" in body  # [3-5,8]
    assert f"\\cite{{{keys[2]},{keys[4]}}}" in body  # superscript 2,4

    # No raw markers survive; the typed reference list is gone from the body.
    assert "{[}" not in body
    assert "\\textsuperscript{2,4}" not in body
    assert "\\section{References}" not in body
    assert "Anomalous Hall effect" not in body

    # Every reconstructed entry is in the .bib, and the flagged one too.
    for key in keys.values():
        assert f"{{{key}," in bib
    assert emit_result.citation_count == 5  # five distinct in-text markers linked


def reconstruct_with_mock(docx: Path):
    with _mock_client() as client:
        return reconstruct_citations(docx, client=client)


def test_emit_project_warns_about_flagged_reference(tmp_path, monkeypatch):
    _ensure_fixture()
    docx = tmp_path / "hand_cited.docx"
    shutil.copy(DOCX, docx)
    monkeypatch.setattr(
        "latextify.citations.crossref.CrossrefClient",
        lambda **kwargs: _mock_client(),
    )

    emit_result = emit_project(docx, "revtex4-2", tmp_path / "output")
    assert any("could not be confidently matched" in w.message for w in emit_result.warnings)
