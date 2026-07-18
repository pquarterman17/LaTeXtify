"""Tests for the metadata/review .docx sanitizer (plan item 3, FORMATS_AND_PRIVACY).

Builds a minimal synthetic .docx by hand (``zipfile`` + literal OOXML
strings) since python-docx cannot author tracked changes, comments, or
hidden runs. Covers: docProps stripped, w:ins accepted (content kept),
w:del accepted (content dropped), comments deleted (part + in-body
markers), hidden (w:vanish) runs dropped, settings.xml rsids scrubbed, and
package consistency ([Content_Types].xml / *.rels no longer reference
anything that was deleted). A round-trip test against the shared
``tests/fixtures/clean.docx`` proves ordinary manuscripts survive with
their visible body text intact.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from latextify.ingest.docx_clean import CleanReport, sanitize_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CLEAN_DOCX = FIXTURE_DIR / "clean.docx"

KNOWN_AUTHOR = "Zzyzx Q. Testauthor"

#: Shortened relationship-type namespace roots, so the literals below can
#: stay within the repo's 100-column line length.
_OFFICEDOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{CT_NS}">
<Default Extension="rels"
 ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/settings.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
<Override PartName="/word/comments.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
<Override PartName="/docProps/core.xml"
 ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml"
 ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL_NS}">
<Relationship Id="rId1" Type="{_OFFICEDOC_REL}/officeDocument"
 Target="word/document.xml"/>
<Relationship Id="rId2" Type="{_PKG_REL}/metadata/core-properties"
 Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="{_OFFICEDOC_REL}/extended-properties"
 Target="docProps/app.xml"/>
</Relationships>"""

_DOCUMENT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL_NS}">
<Relationship Id="rId1" Type="{_OFFICEDOC_REL}/settings" Target="settings.xml"/>
<Relationship Id="rId2" Type="{_OFFICEDOC_REL}/comments" Target="comments.xml"/>
</Relationships>"""

_CORE_PROPS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:creator>{KNOWN_AUTHOR}</dc:creator>
<cp:lastModifiedBy>{KNOWN_AUTHOR}</cp:lastModifiedBy>
</cp:coreProperties>"""

_APP_PROPS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
<Company>Example Corp</Company>
</Properties>"""

_COMMENTS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{W}">
<w:comment w:id="0" w:author="{KNOWN_AUTHOR}" w:date="2026-01-01T00:00:00Z">
<w:p><w:r><w:t>Please double check this claim.</w:t></w:r></w:p>
</w:comment>
</w:comments>"""

_SETTINGS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="{W}">
<w:rsids>
<w:rsidRoot w:val="00000001"/>
<w:rsid w:val="00000002"/>
</w:rsids>
</w:settings>"""

_DOCUMENT_BODY = f"""<w:p>
<w:r><w:t xml:space="preserve">Before insert: </w:t></w:r>
<w:ins w:id="1" w:author="{KNOWN_AUTHOR}" w:date="2026-01-01T00:00:00Z">
<w:r><w:t>INSERTED_TEXT_KEEP</w:t></w:r>
</w:ins>
<w:r><w:t xml:space="preserve"> end.</w:t></w:r>
</w:p>
<w:p>
<w:r><w:t xml:space="preserve">Before delete: </w:t></w:r>
<w:del w:id="2" w:author="{KNOWN_AUTHOR}" w:date="2026-01-01T00:00:00Z">
<w:r><w:delText>DELETED_TEXT_GONE</w:delText></w:r>
</w:del>
<w:r><w:t xml:space="preserve"> end.</w:t></w:r>
</w:p>
<w:p>
<w:commentRangeStart w:id="0"/>
<w:r><w:t>Commented sentence.</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
</w:p>
<w:p>
<w:r><w:rPr><w:vanish/></w:rPr><w:t>HIDDEN_TEXT_GONE</w:t></w:r>
</w:p>
<w:p>
<w:r>
<w:rPr>
<w:b/>
<w:rPrChange w:id="3" w:author="{KNOWN_AUTHOR}" w:date="2026-01-01T00:00:00Z">
<w:rPr/>
</w:rPrChange>
</w:rPr>
<w:t>VISIBLE_TEXT_STAYS</w:t>
</w:r>
</w:p>"""

_DOCUMENT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}"><w:body>{_DOCUMENT_BODY}</w:body></w:document>"""


def _build_synthetic_docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("docProps/core.xml", _CORE_PROPS)
        archive.writestr("docProps/app.xml", _APP_PROPS)
        archive.writestr("word/document.xml", _DOCUMENT_XML)
        archive.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        archive.writestr("word/comments.xml", _COMMENTS)
        archive.writestr("word/settings.xml", _SETTINGS)
    return path


def _all_member_bytes(docx: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(docx) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _document_xml(docx: Path) -> str:
    with zipfile.ZipFile(docx) as archive:
        return archive.read("word/document.xml").decode("utf-8")


@pytest.fixture
def synthetic_docx(tmp_path) -> Path:
    return _build_synthetic_docx(tmp_path / "dirty.docx")


# --------------------------------------------------------------------------- #
# Core scrubbing behaviour
# --------------------------------------------------------------------------- #


def test_known_author_string_is_gone_everywhere(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)

    for name, data in _all_member_bytes(dest).items():
        assert KNOWN_AUTHOR.encode("utf-8") not in data, f"author leaked in {name}"


def test_docprops_parts_are_removed(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)

    names = set(_all_member_bytes(dest))
    assert "docProps/core.xml" not in names
    assert "docProps/app.xml" not in names
    assert report.docprops_stripped is True


def test_inserted_text_survives_deleted_text_is_gone(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)
    xml = _document_xml(dest)

    assert "INSERTED_TEXT_KEEP" in xml
    assert "DELETED_TEXT_GONE" not in xml
    assert f"{{{W}}}ins" not in [el.tag for el in etree.fromstring(xml.encode()).iter()]
    assert f"{{{W}}}del" not in [el.tag for el in etree.fromstring(xml.encode()).iter()]
    assert report.tracked_changes_accepted >= 3  # 1 ins + 1 del + 1 rPrChange


def test_change_tracking_elements_stripped(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)
    xml = _document_xml(dest)

    assert "rPrChange" not in xml
    assert "VISIBLE_TEXT_STAYS" in xml  # the run's own current formatting/text survives


def test_comments_removed_part_and_markers(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)

    names = set(_all_member_bytes(dest))
    assert "word/comments.xml" not in names
    assert "word/_rels/comments.xml.rels" not in names  # orphan .rels would be invalid too

    xml = _document_xml(dest)
    assert "commentReference" not in xml
    assert "commentRangeStart" not in xml
    assert "commentRangeEnd" not in xml
    assert "Commented sentence." in xml  # anchored body text itself is not comment content

    assert report.comments_removed == 1


def test_hidden_run_removed(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)
    xml = _document_xml(dest)

    assert "HIDDEN_TEXT_GONE" not in xml
    assert report.hidden_runs_removed == 1


def test_rsids_scrubbed(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)

    with zipfile.ZipFile(dest) as archive:
        settings_xml = archive.read("word/settings.xml").decode("utf-8")
    assert "rsid" not in settings_xml.lower()
    assert report.rsids_scrubbed is True


def test_report_fields_are_all_populated(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    report = sanitize_docx(synthetic_docx, dest)

    assert isinstance(report, CleanReport)
    assert report.tracked_changes_accepted > 0
    assert report.comments_removed == 1
    assert report.hidden_runs_removed == 1
    assert report.docprops_stripped is True
    assert report.rsids_scrubbed is True


# --------------------------------------------------------------------------- #
# Package consistency: no dangling relationship / content-type entries
# --------------------------------------------------------------------------- #


def test_content_types_no_longer_references_removed_parts(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)

    with zipfile.ZipFile(dest) as archive:
        ct = archive.read("[Content_Types].xml").decode("utf-8")
    assert "docProps/core.xml" not in ct
    assert "docProps/app.xml" not in ct
    assert "comments.xml" not in ct
    assert "word/document.xml" in ct  # untouched parts remain declared


def test_root_rels_no_longer_references_docprops(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)

    with zipfile.ZipFile(dest) as archive:
        rels = archive.read("_rels/.rels").decode("utf-8")
    assert "docProps/core.xml" not in rels
    assert "docProps/app.xml" not in rels
    assert "word/document.xml" in rels  # the real relationship survives


def test_document_rels_drops_comments_keeps_settings(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)

    with zipfile.ZipFile(dest) as archive:
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "comments.xml" not in rels
    assert "settings.xml" in rels


def test_output_is_a_readable_zip_with_valid_document_xml(synthetic_docx, tmp_path):
    dest = tmp_path / "clean.docx"
    sanitize_docx(synthetic_docx, dest)

    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        root = etree.fromstring(archive.read("word/document.xml"))
    assert root.tag == f"{{{W}}}document"


# --------------------------------------------------------------------------- #
# Error contract
# --------------------------------------------------------------------------- #


def test_rejects_non_docx_extension(tmp_path):
    bogus = tmp_path / "manuscript.txt"
    bogus.write_text("not a docx")
    with pytest.raises(ValueError, match=r"\.docx"):
        sanitize_docx(bogus, tmp_path / "out.docx")


def test_rejects_invalid_zip(tmp_path):
    bogus = tmp_path / "broken.docx"
    bogus.write_text("this is not a zip archive")
    with pytest.raises(ValueError, match="not a valid .docx"):
        sanitize_docx(bogus, tmp_path / "out.docx")


def test_rejects_docx_without_document_xml(tmp_path):
    empty = tmp_path / "empty.docx"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
    with pytest.raises(ValueError, match="word/document.xml"):
        sanitize_docx(empty, tmp_path / "out.docx")


# --------------------------------------------------------------------------- #
# Round-trip sanity against a real (ordinary) manuscript fixture
# --------------------------------------------------------------------------- #


def test_roundtrip_clean_fixture_preserves_visible_body_text(tmp_path):
    dest = tmp_path / "clean-sanitized.docx"
    report = sanitize_docx(CLEAN_DOCX, dest)

    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        xml = archive.read("word/document.xml").decode("utf-8")
        etree.fromstring(archive.read("word/document.xml"))  # still parses

    distinctive_sentence = (
        "This manuscript uses only plain paragraphs and standard Word "
        "styles, with nothing for the preflight detectors to flag."
    )
    assert distinctive_sentence in xml
    assert "A Perfectly Ordinary Manuscript" in xml
    assert report.docprops_stripped is True


def test_docprops_thumbnail_is_stripped(tmp_path):
    """A saved first-page preview (docProps/thumbnail.*) is a rendered image of
    the document -- a visual content/identity leak -- so it must be dropped."""
    src = FIXTURE_DIR / "metadata_titlepage.docx"
    with zipfile.ZipFile(src) as archive:
        thumbs = [n for n in archive.namelist() if n.startswith("docProps/thumbnail")]
    assert thumbs, "fixture precondition: source should carry a docProps thumbnail"

    dest = tmp_path / "no-thumb.docx"
    report = sanitize_docx(src, dest)

    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        assert not [n for n in names if n.startswith("docProps/thumbnail")]
        assert archive.testzip() is None
        # The thumbnail's relationship is gone; the shared jpeg Default
        # content-type stays (embedded images may rely on it).
        assert "thumbnail" not in archive.read("_rels/.rels").decode("utf-8").lower()
    assert report.docprops_stripped is True
