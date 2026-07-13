"""Tests for the .docx archive resource-safety guard (audit item 1).

The bounds are overridable per call, so these build a few bytes of synthetic
content against tiny limits instead of materializing a real multi-gigabyte
bomb.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from latextify.ingest.archive_guard import (
    _member_name_is_unsafe as member_name_is_unsafe,
)
from latextify.ingest.archive_guard import (
    stream_zip_member,
    validate_docx_archive,
)
from latextify.ingest.citation_sentinels import rewrite_archive_parts


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# --------------------------------------------------------------------------- #
# Happy path + corrupt-file contract
# --------------------------------------------------------------------------- #


def test_normal_archive_passes(tmp_path):
    """A small, well-formed archive validates with the default bounds."""
    docx = _write_zip(
        tmp_path / "ok.docx",
        {"word/document.xml": b"<x/>", "word/media/image1.png": b"\x89PNG..."},
    )
    validate_docx_archive(docx)  # no raise


def test_non_zip_keeps_corrupt_contract(tmp_path):
    """A non-ZIP file raises the existing 'not a valid .docx' error contract."""
    bogus = tmp_path / "fake.docx"
    bogus.write_text("this is not a zip")
    with pytest.raises(ValueError, match="not a valid .docx"):
        validate_docx_archive(bogus)


# --------------------------------------------------------------------------- #
# Resource-limit rejections (tiny limits, tiny content)
# --------------------------------------------------------------------------- #


def test_rejects_excessive_member_count(tmp_path):
    docx = _write_zip(tmp_path / "many.docx", {f"m{i}": b"x" for i in range(5)})
    with pytest.raises(ValueError, match="members"):
        validate_docx_archive(docx, max_member_count=4)


def test_rejects_oversized_member(tmp_path):
    docx = _write_zip(tmp_path / "big.docx", {"word/document.xml": b"x" * 100})
    with pytest.raises(ValueError, match="expands to 100 bytes"):
        validate_docx_archive(docx, max_member_expanded_bytes=50)


def test_rejects_excessive_total_expanded(tmp_path):
    docx = _write_zip(
        tmp_path / "total.docx",
        {"a": b"x" * 100, "b": b"y" * 100},
    )
    with pytest.raises(ValueError, match="total"):
        validate_docx_archive(
            docx, max_member_expanded_bytes=1000, max_total_expanded_bytes=150
        )


def test_rejects_compression_bomb_ratio(tmp_path):
    """Highly compressible content trips the ratio bound (a small bomb)."""
    docx = _write_zip(tmp_path / "bomb.docx", {"word/document.xml": b"\x00" * 20000})
    with pytest.raises(ValueError, match="compression ratio"):
        validate_docx_archive(
            docx,
            max_member_expanded_bytes=10**9,
            max_total_expanded_bytes=10**9,
            max_compression_ratio=10.0,
            min_compressed_for_ratio=1,
        )


def test_rejects_path_traversal_member(tmp_path):
    docx = _write_zip(
        tmp_path / "trav.docx",
        {"word/document.xml": b"<x/>", "../escape.xml": b"evil"},
    )
    with pytest.raises(ValueError, match="unsafe"):
        validate_docx_archive(docx)


def test_rejects_absolute_member(tmp_path):
    docx = _write_zip(
        tmp_path / "abs.docx",
        {"word/document.xml": b"<x/>", "/etc/passwd": b"evil"},
    )
    with pytest.raises(ValueError, match="unsafe"):
        validate_docx_archive(docx)


def test_rejects_duplicate_normalized_names(tmp_path):
    """Two members colliding under case-folding are duplicates."""
    # zipfile.writestr with duplicate exact names warns but allows it; use a
    # case difference so the normalized keys collide on a case-insensitive FS.
    path = tmp_path / "dup.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/Document.xml", b"<x/>")
        zf.writestr("word/document.xml", b"<y/>")
    with pytest.raises(ValueError, match="duplicate"):
        validate_docx_archive(path)


def _set_encrypted_bit(path: Path) -> None:
    """Flip general-purpose bit 0 in the central-directory header.

    ``zipfile.writestr`` recomputes ``flag_bits`` and won't preserve a pre-set
    encryption bit, and stdlib can't write encrypted members, so we patch the
    2-byte flag field (offset 8 from the ``PK\\x01\\x02`` signature) that
    ``infolist()`` reads.
    """
    data = bytearray(path.read_bytes())
    idx = data.find(b"PK\x01\x02")  # central directory file header
    assert idx != -1, "no central directory header found"
    data[idx + 8] = 0x01
    data[idx + 9] = 0x00
    path.write_bytes(data)


def test_rejects_encrypted_member(tmp_path):
    """A member with the encryption flag bit set is rejected."""
    path = _write_zip(tmp_path / "enc.docx", {"word/document.xml": b"<x/>"})
    _set_encrypted_bit(path)
    # Confirm the flag actually reads back before asserting the guard reacts.
    with zipfile.ZipFile(path) as check:
        assert check.infolist()[0].flag_bits & 0x1
    with pytest.raises(ValueError, match="encrypted"):
        validate_docx_archive(path)


# --------------------------------------------------------------------------- #
# Name-safety helper unit coverage (NUL can't survive zipfile.writestr)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["a\x00b.xml", "/abs.xml", "..\\win.xml", "../rel.xml", "C:\\drive.xml", "a/../b"],
)
def test_member_name_is_unsafe_true(name):
    assert member_name_is_unsafe(name)


@pytest.mark.parametrize("name", ["word/document.xml", "media/image1.png", "[Content_Types].xml"])
def test_member_name_is_unsafe_false(name):
    assert not member_name_is_unsafe(name)


# --------------------------------------------------------------------------- #
# Streaming copy (bounded, faithful) + rewrite round-trip
# --------------------------------------------------------------------------- #


def test_stream_zip_member_copies_faithfully(tmp_path):
    src = _write_zip(tmp_path / "src.zip", {"a.bin": b"hello world" * 100})
    dest = tmp_path / "dest.zip"
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        stream_zip_member(zin, zout, zin.infolist()[0])
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("a.bin") == b"hello world" * 100


def test_stream_zip_member_bounds_actual_bytes(tmp_path):
    """A member larger than max_bytes is rejected mid-copy (lying-header guard)."""
    src = _write_zip(tmp_path / "src.zip", {"a.bin": b"x" * 1000})
    dest = tmp_path / "dest.zip"
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        with pytest.raises(ValueError, match="expands past"):
            stream_zip_member(zin, zout, zin.infolist()[0], max_bytes=100)


def test_rewrite_archive_parts_streams_and_replaces(tmp_path):
    """rewrite_archive_parts replaces the named part and streams the rest intact."""
    src = _write_zip(
        tmp_path / "src.docx",
        {"word/document.xml": b"<old/>", "word/media/image1.png": b"\x89PNG" * 50},
    )
    dest = tmp_path / "dest.docx"
    rewrite_archive_parts(src, dest, {"word/document.xml": b"<new/>"})
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("word/document.xml") == b"<new/>"
        assert zf.read("word/media/image1.png") == b"\x89PNG" * 50
