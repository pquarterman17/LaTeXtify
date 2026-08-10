"""Metadata inspection and sanitizing across formats.

Assertions are made against ``leaky_files.truth.json`` -- the ground truth the
fixture generator planted -- rather than against whatever the code currently
reports, so a regression that stops detecting a leak fails here instead of
quietly narrowing coverage.

The load-bearing test in this file is
:func:`test_sanitize_then_reinspect_is_clean`: it sanitizes each fixture and
runs the inspector over the *output*. A strip that reports success while
leaving metadata behind passes every other kind of test and fails this one.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from latextify.privacy import (
    inspect_file,
    is_supported,
    sanitize_file,
    supported_extensions,
)

FIXTURES = Path(__file__).parent / "fixtures"
TRUTH = json.loads((FIXTURES / "leaky_files.truth.json").read_text())

DECK = FIXTURES / "leaky_deck.pptx"
BOOK = FIXTURES / "leaky_book.xlsx"
PHOTO = FIXTURES / "leaky_photo.jpg"
PDF = FIXTURES / "leaky_doc.pdf"
CLEAN_DOCX = FIXTURES / "clean.docx"

ALL_FIXTURES = [DECK, BOOK, PHOTO, PDF]


def categories(report) -> set[str]:
    return {f.category for f in report.findings}


# ── registry ─────────────────────────────────────────────────────────────


def test_supported_extensions_cover_every_requested_family():
    exts = set(supported_extensions())
    assert {".docx", ".pptx", ".xlsx", ".pdf"} <= exts
    assert {".doc", ".ppt", ".xls"} <= exts, "legacy OLE formats must be routable"
    assert {".jpg", ".png", ".tif"} <= exts


def test_unsupported_extension_names_what_is_supported(tmp_path):
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hello")
    with pytest.raises(ValueError) as exc:
        inspect_file(bogus)
    assert ".txt" in str(exc.value)
    assert ".pptx" in str(exc.value), "the error should list supported formats"


def test_is_supported_is_case_insensitive():
    assert is_supported("DECK.PPTX")
    assert not is_supported("archive.tar.gz")


def test_refuses_to_sanitize_onto_itself(tmp_path):
    target = tmp_path / "deck.pptx"
    target.write_bytes(DECK.read_bytes())
    with pytest.raises(ValueError, match="itself"):
        sanitize_file(target, target)


# ── inspection finds the planted leaks ───────────────────────────────────


def test_deck_inspection_matches_truth():
    truth = TRUTH["deck"]
    report = inspect_file(DECK)
    found = categories(report)

    assert "embedded-workbook" in found, "chart source workbook must be detected"
    assert "speaker-notes" in found
    assert "hidden-slide" in found
    assert "off-canvas" in found
    assert "company" in found
    assert any(truth["author"] in f.summary for f in report.findings)
    assert any(truth["company"] in f.summary for f in report.findings)


def test_workbook_inspection_matches_truth():
    truth = TRUTH["workbook"]
    report = inspect_file(BOOK)
    found = categories(report)

    assert "pivot-cache" in found, "pivot caches retain deleted source data"
    assert "external-link" in found
    assert "hidden-sheet" in found
    assert "hidden-cells" in found
    assert any(truth["company"] in f.summary for f in report.findings)


def test_photo_inspection_finds_gps_and_author():
    truth = TRUTH["photo"]
    report = inspect_file(PHOTO)
    found = categories(report)

    assert "gps" in found
    assert "author" in found
    gps = next(f for f in report.findings if f.category == "gps")
    # The generator planted 39deg 8' 17.48" N, 77deg 13' 11.36" W.
    assert "39.13" in gps.summary and "-77.2" in gps.summary
    assert any(truth["artist"] in f.summary for f in report.findings)


def test_pdf_inspection_detects_failed_redaction():
    report = inspect_file(PDF)
    found = categories(report)

    assert "author" in found
    assert "possible-redaction" in found, (
        "text drawn under a filled black rectangle is the classic PDF leak"
    )
    redaction = next(f for f in report.findings if f.category == "possible-redaction")
    assert not redaction.removable, "redaction cannot be fixed without rasterizing"


def test_inspection_never_writes(tmp_path):
    """Inspection is non-destructive -- byte-identical file afterwards."""
    for source in ALL_FIXTURES:
        target = tmp_path / source.name
        target.write_bytes(source.read_bytes())
        before = target.read_bytes()
        inspect_file(target)
        assert target.read_bytes() == before, f"{source.name} was modified by inspect"


# ── sanitizing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", ALL_FIXTURES, ids=lambda p: p.suffix.lstrip("."))
def test_sanitize_then_reinspect_is_clean(tmp_path, source):
    """The real proof: nothing *removable* survives a sanitize pass."""
    dest = tmp_path / f"clean{source.suffix}"
    report = sanitize_file(source, dest)
    assert dest.is_file() and dest.stat().st_size > 0
    assert report.removed, f"{source.name} should have had something to remove"

    after = inspect_file(dest)
    leftover = [f for f in after.findings if f.removable]
    assert not leftover, (
        f"{source.name}: sanitizing reported success but these remain: "
        + ", ".join(f"{f.category}={f.summary}" for f in leftover)
    )


@pytest.mark.parametrize("source", ALL_FIXTURES, ids=lambda p: p.suffix.lstrip("."))
def test_every_handler_accepts_the_shared_option_set(tmp_path, source):
    """registry.sanitize_file promises unknown options are ignored.

    The CLI passes one uniform option set to every format, so a handler that
    does not tolerate a key meant for a different format raises TypeError on a
    path no format-specific test exercises. (This is exactly how `latextify
    clean` on a .pptx broke: pptx.sanitize lacked **options while the CLI
    passed keep_color_profile.)
    """
    dest = tmp_path / f"opts{source.suffix}"
    sanitize_file(
        source,
        dest,
        keep_notes=False,
        keep_color_profile=True,
        an_option_no_handler_knows=object(),
    )
    assert dest.is_file()


def test_sanitized_deck_drops_the_embedded_workbook_and_notes(tmp_path):
    dest = tmp_path / "clean.pptx"
    sanitize_file(DECK, dest)
    with zipfile.ZipFile(dest) as zin:
        names = zin.namelist()
    assert not [n for n in names if n.startswith("ppt/embeddings/")]
    assert not [n for n in names if n.startswith("ppt/notesSlides/")]
    assert not [n for n in names if n.startswith("docProps/")]


def test_sanitized_deck_removes_the_hidden_slide_and_its_list_entry(tmp_path):
    dest = tmp_path / "clean.pptx"
    with zipfile.ZipFile(DECK) as zin:
        before = len([n for n in zin.namelist() if n.startswith("ppt/slides/slide")])
    sanitize_file(DECK, dest)
    with zipfile.ZipFile(dest) as zin:
        after_names = zin.namelist()
        presentation = zin.read("ppt/presentation.xml").decode()
    after = len([n for n in after_names if n.startswith("ppt/slides/slide")])

    assert after == before - 1, "the hidden slide part should be gone"
    # Every remaining sldId must still resolve, or PowerPoint refuses the file.
    rels = _relationship_ids(dest, "ppt/_rels/presentation.xml.rels")
    referenced = set(_sld_relationship_ids(presentation))
    assert referenced <= rels, "presentation.xml references a relationship that is gone"


def test_sanitized_packages_stay_internally_consistent(tmp_path):
    """No content-type Override or relationship may point at a removed part."""
    for source in (DECK, BOOK):
        dest = tmp_path / f"consistent{source.suffix}"
        sanitize_file(source, dest)
        with zipfile.ZipFile(dest) as zin:
            names = set(zin.namelist())
            content_types = zin.read("[Content_Types].xml").decode()
            for member in names:
                if not member.endswith(".rels"):
                    continue
                for target in _rels_targets(zin.read(member), member):
                    assert target in names, (
                        f"{source.name}: {member} points at missing part {target}"
                    )
        for override in _overrides(content_types):
            assert override.lstrip("/") in names, (
                f"{source.name}: content types declare missing part {override}"
            )


def test_sanitized_workbook_drops_pivot_cache_but_keeps_hidden_sheet(tmp_path):
    """Hidden sheets are reported, not deleted -- formulas reference them."""
    dest = tmp_path / "clean.xlsx"
    report = sanitize_file(BOOK, dest)
    with zipfile.ZipFile(dest) as zin:
        names = zin.namelist()
        workbook = zin.read("xl/workbook.xml").decode()

    assert not [n for n in names if n.startswith("xl/pivotCache/")]
    assert not [n for n in names if n.startswith("xl/externalLinks/")]
    assert "RawSalaries" in workbook, "hidden sheet must be preserved, not silently cut"
    assert any("hidden sheet" in w for w in report.warnings), (
        "the user must be told the hidden sheet is still there"
    )


def test_sanitized_photo_loses_exif_but_keeps_pixels(tmp_path):
    from PIL import Image

    dest = tmp_path / "clean.jpg"
    sanitize_file(PHOTO, dest)

    with Image.open(PHOTO) as before, Image.open(dest) as after:
        assert after.size == before.size
        assert not after.getexif(), "EXIF must be gone entirely"


def test_sanitized_pdf_clears_info_and_warns_about_redaction(tmp_path):
    from pypdf import PdfReader

    dest = tmp_path / "clean.pdf"
    report = sanitize_file(PDF, dest)

    reader = PdfReader(str(dest))
    info = reader.metadata or {}
    assert not (info.get("/Author") or "").strip()
    assert not (info.get("/Title") or "").strip()
    assert any("NOT FIXED" in w and "redaction" in w.lower() for w in report.warnings), (
        "an unfixable content leak must be surfaced, never silently dropped"
    )


def test_docx_still_routes_through_the_existing_sanitizer(tmp_path):
    dest = tmp_path / "clean_out.docx"
    report = sanitize_file(CLEAN_DOCX, dest)
    assert dest.is_file()
    assert report.file_format == "Word document"
    with zipfile.ZipFile(dest) as zin:
        assert not [n for n in zin.namelist() if n.startswith("docProps/")]


# ── legacy OLE ───────────────────────────────────────────────────────────


def test_legacy_sanitize_refuses_with_an_actionable_remedy(tmp_path):
    legacy = tmp_path / "old.doc"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    with pytest.raises(ValueError) as exc:
        sanitize_file(legacy, tmp_path / "out.doc")
    message = str(exc.value)
    assert "Save As" in message, "must name the remedy that actually works"
    assert ".docx" in message
    assert "latextify inspect" in message


def test_legacy_inspect_rejects_a_non_ole_file(tmp_path):
    fake = tmp_path / "actually_modern.doc"
    fake.write_bytes(DECK.read_bytes())
    with pytest.raises(ValueError, match="not a legacy OLE2"):
        inspect_file(fake)


# ── report model ─────────────────────────────────────────────────────────


def test_findings_sort_worst_first():
    report = inspect_file(DECK)
    severities = [f.severity for f in report.sorted_findings()]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)


def test_unremovable_findings_are_separable():
    report = inspect_file(BOOK)
    assert report.unremovable, "hidden sheets are reported as unfixable"
    assert all(not f.removable for f in report.unremovable)


# ── helpers ──────────────────────────────────────────────────────────────


def _rels_targets(data: bytes, member: str) -> list[str]:
    from lxml import etree

    from latextify.privacy import opc

    root = etree.fromstring(data)
    base = opc.rels_base_dir(member)
    targets = []
    for rel in root:
        if not isinstance(rel.tag, str) or rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target")
        if target:
            targets.append(opc.resolve_target(base, target))
    return targets


def _relationship_ids(path: Path, member: str) -> set[str]:
    from lxml import etree

    with zipfile.ZipFile(path) as zin:
        root = etree.fromstring(zin.read(member))
    return {rel.get("Id") for rel in root if isinstance(rel.tag, str)}


def _sld_relationship_ids(presentation_xml: str) -> list[str]:
    import re

    return re.findall(r'<p:sldId[^>]*r:id="([^"]+)"', presentation_xml)


def _overrides(content_types_xml: str) -> list[str]:
    import re

    return re.findall(r'<Override PartName="([^"]+)"', content_types_xml)
