from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from latextify.model.compile import CompileResult
from latextify.system_check import run_system_check


def test_system_check_reports_offline_components_without_user_content(tmp_path: Path, monkeypatch):
    tectonic = tmp_path / "tectonic.exe"
    tectonic.write_bytes(b"binary")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("LATEXTIFY_OFFLINE", "1")
    monkeypatch.setenv("TECTONIC_CACHE_DIR", str(cache))
    monkeypatch.setattr("latextify.system_check.find_tectonic", lambda: tectonic)
    monkeypatch.setattr("latextify.system_check.pypandoc.get_pandoc_version", lambda: "3.1")
    monkeypatch.setattr(
        "latextify.system_check.pypandoc.convert_text", lambda *a, **k: "LaTeXtify diagnostic"
    )
    monkeypatch.setattr(
        "latextify.system_check.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="tectonic 0.15", stderr=""),
    )
    monkeypatch.setattr(
        "latextify.system_check.compile_document",
        lambda *a, **k: CompileResult(
            success=True, pdf_path=tmp_path / "main.pdf", diagnostics=(), raw_log="", returncode=0
        ),
    )
    monkeypatch.setattr("latextify.system_check._find_metafile_converter", lambda: None)

    result = run_system_check(tmp_path / "work")
    assert result["overall"] == "warn"  # raster fallback is useful but worth surfacing
    assert "offline mode" in result["report"]
    assert "manuscript content" in result["report"]
    assert str(tmp_path) not in result["report"]
