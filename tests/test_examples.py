"""The examples/ scripts must keep converting cleanly (plan item 8).

Each example ships a ``make_manuscript.py`` whose ``build()`` writes the input
document(s), and a ``run.py`` that converts them. This test drives the real
generators and runs ``emit_project`` (no ``--pdf``, so it needs no Tectonic
and no network) to prove every example still produces a valid LaTeX project.
Crossref is neutralised for the plaintext example so the assertions are
deterministic and offline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from latextify.emit.project import emit_project

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _load_generator(example_dir: str):
    """Import an example's make_manuscript.py under a unique module name."""
    path = EXAMPLES / example_dir / "make_manuscript.py"
    mod_name = f"example_gen_{example_dir.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def test_example_01_all_embedded(tmp_path, monkeypatch):
    # Force the offline Crossref path so the bibliography is deterministic:
    # every typed reference degrades to a verify-flagged raw entry.
    from latextify.citations import crossref

    monkeypatch.setattr(
        crossref.CrossrefClient, "query_bibliographic", lambda self, *a, **k: []
    )

    gen = _load_generator("01-all-embedded")
    docx = gen.build()
    assert docx.is_file()

    result = emit_project(docx, "revtex4-2", tmp_path / "output", report=False)

    assert result.main_tex_path.is_file()
    assert result.bib_path.read_text(encoding="utf-8").strip()  # raw entries written
    # Two embedded figures were extracted and copied.
    assert result.figure_count == 2
    assert (result.figures_dir / "fig1.png").is_file()
    assert (result.figures_dir / "fig2.png").is_file()


def test_example_02_word_plus_figures(tmp_path):
    gen = _load_generator("02-word-plus-figures")
    docx = gen.build()
    assert docx.is_file()

    result = emit_project(docx, "revtex4-2", tmp_path / "output", report=False)

    assert result.main_tex_path.is_file()
    # Figure 1 comes from the figures/ folder convention, figure 2 from the
    # figures.yaml manifest -- both external, neither embedded.
    sources = {fig.number: fig.source.value for fig in result.figures}
    assert sources == {1: "override", 2: "manifest"}


def test_example_03_multipart_refmanager(tmp_path):
    gen = _load_generator("03-multipart-refmanager")
    gen.build()
    main_docx = EXAMPLES / "03-multipart-refmanager" / "main.docx"
    supplement_docx = EXAMPLES / "03-multipart-refmanager" / "supplement.docx"
    assert main_docx.is_file() and supplement_docx.is_file()

    result = emit_project(
        main_docx, "revtex4-2", tmp_path / "output",
        supplement_docx_path=supplement_docx, report=False,
    )

    # paper.yaml drove the metadata (two named authors), no guessing.
    metadata = result.metadata_tex_path.read_text(encoding="utf-8")
    assert "Dana R. Leadauthor" in metadata and "Evan S. Coauthor" in metadata

    # Field codes carry full metadata -> DOI-bearing entries, no Crossref.
    bib = result.bib_path.read_text(encoding="utf-8")
    assert "10.1038/nphys3465" in bib  # Cornelissen (cited in BOTH docs)
    assert "10.1038/nphys3347" in bib  # Chumak (main)
    assert "10.1038/nature08876" in bib  # Kajiwara (supplement)

    # Cornelissen is cited in main AND supplement but de-dupes to one entry.
    assert bib.count("10.1038/nphys3465") == 1
    assert result.supplement is not None
    assert result.supplement.new_reference_count == 1  # only Kajiwara is new


@pytest.mark.parametrize("example_dir", ["01-all-embedded", "02-word-plus-figures",
                                         "03-multipart-refmanager"])
def test_example_has_readme_and_runner(example_dir):
    base = EXAMPLES / example_dir
    assert (base / "README.md").is_file()
    assert (base / "run.py").is_file()
    assert (base / "make_manuscript.py").is_file()
