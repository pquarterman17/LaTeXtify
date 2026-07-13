"""Offline tests for the pinned, checksum-verified Tectonic bootstrap (audit item 2).

All synthetic: tiny in-memory archives and an ``httpx.MockTransport`` stand in
for the real GitHub download, so nothing here contacts the network or needs a
real binary. Real-download tests stay in the ``tectonic``/``network`` marked
suites.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from latextify.compile import tectonic
from latextify.compile.tectonic import (
    TectonicNotAvailableError,
    _asset_for_triple,
    _download_verified_archive,
    _safe_extract_binary,
    download_tectonic_release,
)

# --------------------------------------------------------------------------- #
# Synthetic-archive builders
# --------------------------------------------------------------------------- #


def _make_tar(path: Path, members: list[tuple[str, bytes]], *, symlink: str | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
        if symlink is not None:
            link = tarfile.TarInfo(symlink)
            link.type = tarfile.SYMTYPE
            link.linkname = "elsewhere"
            tf.addfile(link)
    return path


def _make_zip(path: Path, members: list[tuple[str, bytes]], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, b"elsewhere")
    return path


def _mock_client(body: bytes) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body)),
        follow_redirects=True,
    )


# --------------------------------------------------------------------------- #
# Pinned-asset lookup fails closed
# --------------------------------------------------------------------------- #


def test_asset_for_triple_known_returns_name_and_hash():
    name, sha = _asset_for_triple("x86_64-unknown-linux-gnu")
    assert name.endswith(".tar.gz")
    assert len(sha) == 64  # sha256 hex


def test_asset_for_triple_unknown_fails_closed():
    with pytest.raises(TectonicNotAvailableError, match="No pinned Tectonic"):
        _asset_for_triple("sparc-unknown-foo")


# --------------------------------------------------------------------------- #
# Safe extraction: only the one root-level binary, everything else rejected
# --------------------------------------------------------------------------- #


def test_extract_tar_binary_extracts_only_the_binary(tmp_path):
    archive = _make_tar(tmp_path / "t.tar.gz", [("tectonic", b"ELF-ish"), ("README", b"x")])
    dest_dir = tmp_path / "cache"
    out = _safe_extract_binary(archive, "t.tar.gz", dest_dir, "tectonic")
    assert out == dest_dir / "tectonic"
    assert out.read_bytes() == b"ELF-ish"
    assert not (dest_dir / "README").exists()  # only the binary member is written
    if os.name == "posix":
        assert out.stat().st_mode & stat.S_IXUSR


def test_extract_zip_binary_extracts_only_the_binary(tmp_path):
    archive = _make_zip(tmp_path / "t.zip", [("tectonic.exe", b"MZ-ish"), ("LICENSE", b"x")])
    dest_dir = tmp_path / "cache"
    out = _safe_extract_binary(archive, "t.zip", dest_dir, "tectonic.exe")
    assert out.read_bytes() == b"MZ-ish"
    assert not (dest_dir / "LICENSE").exists()


def test_extract_missing_binary_fails(tmp_path):
    archive = _make_tar(tmp_path / "t.tar.gz", [("README", b"x")])
    with pytest.raises(TectonicNotAvailableError, match="no root-level tectonic"):
        _safe_extract_binary(archive, "t.tar.gz", tmp_path / "cache", "tectonic")


def test_extract_duplicate_binary_fails(tmp_path):
    archive = _make_tar(tmp_path / "t.tar.gz", [("tectonic", b"a"), ("tectonic", b"b")])
    with pytest.raises(TectonicNotAvailableError, match="multiple tectonic"):
        _safe_extract_binary(archive, "t.tar.gz", tmp_path / "cache", "tectonic")


def test_extract_nested_member_is_not_matched(tmp_path):
    # A binary hidden under a subdirectory is not a root-level member.
    archive = _make_tar(tmp_path / "t.tar.gz", [("sub/tectonic", b"x")])
    with pytest.raises(TectonicNotAvailableError, match="no root-level tectonic"):
        _safe_extract_binary(archive, "t.tar.gz", tmp_path / "cache", "tectonic")


def test_extract_traversal_member_is_not_matched(tmp_path):
    archive = _make_tar(tmp_path / "t.tar.gz", [("../tectonic", b"x")])
    with pytest.raises(TectonicNotAvailableError, match="no root-level tectonic"):
        _safe_extract_binary(archive, "t.tar.gz", tmp_path / "cache", "tectonic")


def test_extract_tar_symlink_binary_fails(tmp_path):
    archive = _make_tar(tmp_path / "t.tar.gz", [], symlink="tectonic")
    with pytest.raises(TectonicNotAvailableError, match="not a regular file"):
        _safe_extract_binary(archive, "t.tar.gz", tmp_path / "cache", "tectonic")


def test_extract_zip_symlink_binary_fails(tmp_path):
    archive = _make_zip(tmp_path / "t.zip", [], symlink="tectonic")
    with pytest.raises(TectonicNotAvailableError, match="symlink"):
        _safe_extract_binary(archive, "t.zip", tmp_path / "cache", "tectonic")


# --------------------------------------------------------------------------- #
# Download: size cap + checksum
# --------------------------------------------------------------------------- #


def test_download_verified_good_hash_writes_file(tmp_path):
    body = b"archive-bytes"
    sha = hashlib.sha256(body).hexdigest()
    dest = tmp_path / "a.tar.gz"
    with _mock_client(body) as client:
        _download_verified_archive("https://x/y", sha, dest, client=client)
    assert dest.read_bytes() == body


def test_download_verified_bad_hash_fails(tmp_path):
    body = b"archive-bytes"
    with _mock_client(body) as client:
        with pytest.raises(TectonicNotAvailableError, match="checksum mismatch"):
            _download_verified_archive("https://x/y", "00" * 32, tmp_path / "a", client=client)


def test_download_verified_oversized_fails(tmp_path):
    body = b"x" * 5000
    sha = hashlib.sha256(body).hexdigest()
    with _mock_client(body) as client:
        with pytest.raises(TectonicNotAvailableError, match="exceeded"):
            _download_verified_archive(
                "https://x/y", sha, tmp_path / "a", client=client, max_bytes=1000
            )


# --------------------------------------------------------------------------- #
# End-to-end download_tectonic_release (pinned manifest + mock transport)
# --------------------------------------------------------------------------- #


def test_download_release_end_to_end(tmp_path, monkeypatch):
    archive_path = _make_tar(tmp_path / "src.tar.gz", [("tectonic", b"THE-BINARY")])
    body = archive_path.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(
        tectonic, "_TECTONIC_ASSETS", {"test-triple": ("tectonic-test.tar.gz", sha)}
    )
    dest_dir = tmp_path / "cache"
    with _mock_client(body) as client:
        out = download_tectonic_release("test-triple", "tectonic", dest_dir, client=client)
    assert out == dest_dir / "tectonic"
    assert out.read_bytes() == b"THE-BINARY"
    assert not list(dest_dir.glob("*.partial"))  # atomic swap left no temp file


def test_bad_download_leaves_prior_cached_binary_intact(tmp_path, monkeypatch):
    # A verification failure must not clobber a good cached binary.
    dest_dir = tmp_path / "cache"
    dest_dir.mkdir()
    (dest_dir / "tectonic").write_bytes(b"OLD-GOOD-BINARY")

    monkeypatch.setattr(
        tectonic, "_TECTONIC_ASSETS", {"test-triple": ("tectonic-test.tar.gz", "00" * 32)}
    )
    body = _make_tar(tmp_path / "src.tar.gz", [("tectonic", b"NEW")]).read_bytes()
    with _mock_client(body) as client:
        with pytest.raises(TectonicNotAvailableError, match="checksum mismatch"):
            download_tectonic_release("test-triple", "tectonic", dest_dir, client=client)
    assert (dest_dir / "tectonic").read_bytes() == b"OLD-GOOD-BINARY"  # untouched
