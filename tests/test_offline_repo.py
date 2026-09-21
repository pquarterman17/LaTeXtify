from __future__ import annotations

import zipfile
from pathlib import Path

from tools import build_windows_offline_repo as offline_repo


def test_copy_checkout_copies_only_reported_tracked_files(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "package").mkdir(parents=True)
    (source / "package" / "tracked.py").write_text("tracked")
    (source / "ignored.txt").write_text("ignored")
    monkeypatch.setattr(offline_repo, "tracked_files", lambda _repo: [Path("package/tracked.py")])

    offline_repo._copy_checkout(source, destination)

    assert (destination / "package" / "tracked.py").read_text() == "tracked"
    assert not (destination / "ignored.txt").exists()


def test_zip_tree_has_one_top_level_bundle_directory(tmp_path: Path) -> None:
    bundle = tmp_path / offline_repo.BUNDLE_NAME
    (bundle / "latextify").mkdir(parents=True)
    (bundle / "startup.bat").write_text("@echo off\n")
    (bundle / "latextify" / "__init__.py").write_text("")
    archive = tmp_path / "bundle.zip"

    offline_repo._zip_tree(bundle, archive)

    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == [
            f"{offline_repo.BUNDLE_NAME}/latextify/__init__.py",
            f"{offline_repo.BUNDLE_NAME}/startup.bat",
        ]


def test_windows_startup_is_offline_first() -> None:
    source = (offline_repo.ROOT / "startup.bat").read_text(encoding="utf-8")
    folded = source.casefold()
    assert ".runtime\\python\\python.exe" in folded
    assert ".offline-kit\\install.py" in folded
    assert '--dir "%cd%"' in folded
    assert '--dir "%~dp0"' not in folded
    assert 'if /i not "%latextify_allow_downloads%"=="1"' in folded
    assert "goto no_offline_runtime" in folded


def test_online_bootstrap_requires_explicit_wrapper() -> None:
    wrapper = (offline_repo.ROOT / "startup-online.bat").read_text(encoding="utf-8")
    assert 'set "LATEXTIFY_ALLOW_DOWNLOADS=1"' in wrapper
    assert 'call "%~dp0startup.bat" %*' in wrapper


def test_offline_repo_builder_adds_integrity_tools() -> None:
    source = (offline_repo.ROOT / "tools" / "build_windows_offline_repo.py").read_text(
        encoding="utf-8"
    )
    assert "Verify-LaTeXtify.bat" in source
    assert "verify_installation.py" in source
    assert "write_manifest(bundle_dir)" in source
