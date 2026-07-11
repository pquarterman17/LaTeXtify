"""Tests for the Tectonic binary wrapper.

Binary detection and vendored-file staging are pure/mocked -- no network or
real binary needed, run unconditionally.

The `TestRealCompiles` class exercises the actual `tectonic -X compile`
invocation (binary download/cache + real compiles) and is marked
`@pytest.mark.tectonic`, skipped only if no Tectonic binary can be found on
PATH, in the platformdirs cache, or downloaded fresh right now.
"""

from __future__ import annotations

import shutil

import pytest

from latextify.compile import tectonic
from latextify.compile.tectonic import (
    compile_document,
    ensure_tectonic,
    find_tectonic,
    stage_vendor_files,
)
from latextify.model.compile import DiagnosticSeverity

# --- Binary detection (mocked -- no real binary/network needed) -------------


def test_find_tectonic_prefers_path(monkeypatch, tmp_path):
    fake = tmp_path / "tectonic.exe"
    fake.write_text("fake")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))
    assert find_tectonic() == fake


def test_find_tectonic_falls_back_to_cache_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    fake_cache = tmp_path / "cache"
    fake_cache.mkdir()
    binary_name = "tectonic.exe" if tectonic.platform.system() == "Windows" else "tectonic"
    (fake_cache / binary_name).write_text("fake")
    monkeypatch.setattr(tectonic, "cache_dir", lambda: fake_cache)
    assert find_tectonic() == fake_cache / binary_name


def test_find_tectonic_returns_none_when_absent_everywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(tectonic, "cache_dir", lambda: tmp_path / "nonexistent")
    assert find_tectonic() is None


def test_path_takes_priority_over_cache(monkeypatch, tmp_path):
    on_path = tmp_path / "path" / "tectonic.exe"
    on_path.parent.mkdir()
    on_path.write_text("fake")
    cached = tmp_path / "cache" / "tectonic.exe"
    cached.parent.mkdir()
    cached.write_text("fake")

    monkeypatch.setattr(shutil, "which", lambda name: str(on_path))
    monkeypatch.setattr(tectonic, "cache_dir", lambda: cached.parent)

    assert find_tectonic() == on_path


# --- Vendored-file staging (pure filesystem, no binary/network needed) ------


def test_stage_vendor_files_copies_into_workdir(tmp_path):
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "fake-journal.sty").write_text("\\ProvidesPackage{fake-journal}\n")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    staged = stage_vendor_files(vendor_dir, workdir)

    dest = workdir / "fake-journal.sty"
    assert staged == [dest]
    assert dest.is_file()
    assert dest.read_text() == "\\ProvidesPackage{fake-journal}\n"


def test_stage_vendor_files_noop_when_vendor_dir_missing(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    assert stage_vendor_files(tmp_path / "no-such-vendor", workdir) == []


def test_stage_vendor_files_ignores_subdirectories(tmp_path):
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "sub").mkdir()
    (vendor_dir / "sub" / "nested.sty").write_text("x")
    (vendor_dir / "top.cls").write_text("y")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    staged = stage_vendor_files(vendor_dir, workdir)

    assert [p.name for p in staged] == ["top.cls"]


# --- Real binary/network tests -----------------------------------------------


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

PLAIN_ARTICLE = r"""
\documentclass{article}
\begin{document}
Hello, world!
\end{document}
"""

REVTEX_HELLO_WORLD = r"""
\documentclass[aps,prb,reprint]{revtex4-2}
\begin{document}
\title{Hello REVTeX}
\author{A. Author}
\affiliation{Some University}
\begin{abstract}
A minimal abstract.
\end{abstract}
\maketitle
Hello, world!
\end{document}
"""

PLANTED_ERROR_DOC = r"""
\documentclass{article}
\begin{document}
Hello, world! \undefinedcommand{oops}
\end{document}
"""

MISSING_CLASS_DOC = r"""
\documentclass{totallyFakeJournalClassForTesting}
\begin{document}
Hello, world!
\end{document}
"""

FAKE_CLASS_SOURCE = (
    "\\NeedsTeXFormat{LaTeX2e}\n"
    "\\ProvidesClass{totallyFakeJournalClassForTesting}[2026/01/01 fake vendored class]\n"
    "\\LoadClass{article}\n"
)


@requires_tectonic
@skip_without_tectonic
class TestRealCompiles:
    def test_plain_article_hello_world_produces_pdf(self, tmp_path):
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(PLAIN_ARTICLE)

        result = compile_document(tex_path)

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        assert result.errors == ()

    def test_revtex42_hello_world_produces_pdf(self, tmp_path):
        """The de-risk gate: does Tectonic's bundle provide revtex4-2?"""
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(REVTEX_HELLO_WORLD)

        result = compile_document(tex_path)

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        assert result.pdf_path.stat().st_size > 0

    def test_planted_error_yields_structured_diagnostic_not_raw_spew(self, tmp_path):
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(PLANTED_ERROR_DOC)

        result = compile_document(tex_path)

        assert not result.success
        assert len(result.errors) >= 1
        error = result.errors[0]
        assert error.severity is DiagnosticSeverity.ERROR
        assert error.file == "main.tex"
        assert error.line == 4
        assert "Undefined control sequence" in error.message
        # Structured diagnostics are short, single-purpose objects -- not the
        # multi-KB raw log dump (which is still available via .raw_log for
        # the report, but callers reading .diagnostics never see it).
        assert len(error.message) < 200

    def test_missing_class_fails_without_vendoring(self, tmp_path):
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(MISSING_CLASS_DOC)

        result = compile_document(tex_path)

        assert not result.success
        assert any("totallyFakeJournalClassForTesting" in d.message for d in result.errors)

    def test_missing_class_resolved_by_vendoring(self, tmp_path):
        tex_path = tmp_path / "main.tex"
        tex_path.write_text(MISSING_CLASS_DOC)
        vendor_dir = tmp_path / "vendor"
        vendor_dir.mkdir()
        (vendor_dir / "totallyFakeJournalClassForTesting.cls").write_text(FAKE_CLASS_SOURCE)

        result = compile_document(tex_path, vendor_dir=vendor_dir)

        assert result.success, result.raw_log
        assert result.pdf_path is not None
        assert result.pdf_path.is_file()
        # staging happened into the same dir compile ran in
        assert (tmp_path / "totallyFakeJournalClassForTesting.cls").is_file()
