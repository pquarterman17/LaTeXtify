from __future__ import annotations

from pathlib import Path

import pytest

from latextify.integrity import format_result, verify_manifest, write_manifest


def test_manifest_detects_missing_changed_and_ignores_runtime_files(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "two.txt").write_text("two", encoding="utf-8")
    write_manifest(tmp_path)

    (tmp_path / "one.txt").write_text("changed", encoding="utf-8")
    (tmp_path / "nested" / "two.txt").unlink()
    (tmp_path / "extra.txt").write_text("extra", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.txt").write_text("ignored", encoding="utf-8")

    result = verify_manifest(tmp_path)
    assert not result.ok
    assert result.changed == ("one.txt",)
    assert result.missing == ("nested/two.txt",)
    assert result.unexpected == ("extra.txt",)
    assert "FAIL" in format_result(result)


def test_manifest_rejects_parent_traversal(tmp_path: Path) -> None:
    (tmp_path / "INSTALL-MANIFEST.sha256").write_text("0" * 64 + "  ../escape\n")
    with pytest.raises(ValueError, match="unsafe"):
        verify_manifest(tmp_path)


def test_manifest_rejects_non_hex_digest(tmp_path: Path) -> None:
    (tmp_path / "INSTALL-MANIFEST.sha256").write_text("z" * 64 + "  file.txt\n")
    with pytest.raises(ValueError, match="invalid"):
        verify_manifest(tmp_path)
