"""Tests for latextify.ingest.preflight_privacy (METADATA_PRIVACY_PLAN item 15).

Builds tiny synthetic .docx fixtures at test time via ``zipfile`` + literal
OOXML strings -- the same technique ``tests/test_docx_clean.py`` uses, since
python-docx cannot author tracked changes or comments -- rather than adding
new committed binaries the existing fixtures don't fit
(``tests/fixtures/clean.docx`` is structurally clean but is *not*
privacy-clean: it carries a real author/description/thumbnail from
python-docx, which is exactly why it now trips several findings itself; see
``test_clean_docx_yields_zero_structural_findings`` in test_preflight.py).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from latextify.ingest import preflight_privacy
from latextify.ingest.preflight import run_preflight
from latextify.model.preflight import Severity
from latextify.privacy import docx_adapter
from latextify.report.render import render_report, write_report

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN_DOCX = FIXTURES / "clean.docx"

# A tracked insertion AND a review comment -- also trips the existing
# structural `tracked_changes` ERROR detector (unresolved tracked changes
# really do break the pandoc conversion), which is correct, pre-existing
# behavior unrelated to this feature.
_TRACKED_AND_COMMENTED_DOCUMENT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}"><w:body>
<w:p>
<w:r><w:t xml:space="preserve">Before insert: </w:t></w:r>
<w:ins w:id="1" w:author="Reviewer" w:date="2026-01-01T00:00:00Z">
<w:r><w:t>added text</w:t></w:r>
</w:ins>
</w:p>
<w:p>
<w:commentRangeStart w:id="0"/>
<w:r><w:t>Commented sentence.</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
</w:p>
</w:body></w:document>"""

# A review comment only, no tracked change -- demonstrates the privacy layer
# is informational on its own: it never trips the structural ERROR detector.
_COMMENTED_ONLY_DOCUMENT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}"><w:body>
<w:p>
<w:commentRangeStart w:id="0"/>
<w:r><w:t>Commented sentence.</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
</w:p>
</w:body></w:document>"""

_COMMENTS_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{W}">
<w:comment w:id="0" w:author="Reviewer" w:date="2026-01-01T00:00:00Z">
<w:p><w:r><w:t>Please double check this.</w:t></w:r></w:p>
</w:comment>
</w:comments>"""

# No docProps, no tracked changes, no comments -- inspect() should find
# nothing at all.
_PRIVACY_CLEAN_DOCUMENT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>Nothing to see here.</w:t></w:r></w:p>
</w:body></w:document>"""


def _write_docx(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _tracked_and_commented_docx(tmp_path: Path) -> Path:
    return _write_docx(
        tmp_path / "tracked_and_commented.docx",
        {
            "word/document.xml": _TRACKED_AND_COMMENTED_DOCUMENT_XML,
            "word/comments.xml": _COMMENTS_XML,
        },
    )


def _commented_only_docx(tmp_path: Path) -> Path:
    return _write_docx(
        tmp_path / "commented_only.docx",
        {
            "word/document.xml": _COMMENTED_ONLY_DOCUMENT_XML,
            "word/comments.xml": _COMMENTS_XML,
        },
    )


def _privacy_clean_docx(tmp_path: Path) -> Path:
    return _write_docx(
        tmp_path / "privacy_clean.docx", {"word/document.xml": _PRIVACY_CLEAN_DOCUMENT_XML}
    )


# --------------------------------------------------------------------------- #
# privacy_findings(): the Finding -> PreflightFinding translation
# --------------------------------------------------------------------------- #


def test_tracked_changes_and_comments_surface_as_warn(tmp_path):
    docx = _tracked_and_commented_docx(tmp_path)
    findings = preflight_privacy.privacy_findings(docx)
    by_detector = {f.detector: f for f in findings}

    assert "privacy_tracked-changes" in by_detector
    tracked = by_detector["privacy_tracked-changes"]
    assert tracked.severity is Severity.WARN
    assert "tracked change" in tracked.message.lower()

    assert "privacy_comments" in by_detector
    comments = by_detector["privacy_comments"]
    assert comments.severity is Severity.WARN
    assert "comment" in comments.message.lower()


def test_findings_use_the_paragraph_index_sentinel_for_document_level_location(tmp_path):
    """docx_adapter inspects whole package parts, not a body paragraph, so
    these findings carry no real body location -- same -1 "not applicable"
    sentinel `ingest.metadata_guess` already uses."""
    docx = _tracked_and_commented_docx(tmp_path)
    findings = preflight_privacy.privacy_findings(docx)
    assert findings
    assert all(f.location.paragraph_index == -1 for f in findings)


def test_privacy_findings_are_never_a_hard_error(tmp_path):
    docx = _tracked_and_commented_docx(tmp_path)
    findings = preflight_privacy.privacy_findings(docx)
    assert findings
    assert all(f.severity is not Severity.ERROR for f in findings)


def test_privacy_clean_docx_produces_no_findings(tmp_path):
    docx = _privacy_clean_docx(tmp_path)
    assert preflight_privacy.privacy_findings(docx) == ()


def test_non_docx_manuscript_produces_no_findings_and_no_error(tmp_path):
    # Extension check happens before any file I/O, so the path need not exist.
    assert preflight_privacy.privacy_findings(tmp_path / "manuscript.md") == ()
    assert preflight_privacy.privacy_findings(tmp_path / "manuscript.odt") == ()
    assert preflight_privacy.privacy_findings(tmp_path / "manuscript.rtf") == ()


def test_failed_inspection_degrades_to_one_info_note(monkeypatch):
    def _boom(_path):
        raise RuntimeError("simulated docx_adapter failure")

    monkeypatch.setattr(docx_adapter, "inspect", _boom)
    findings = preflight_privacy.privacy_findings(CLEAN_DOCX)

    assert len(findings) == 1
    assert findings[0].severity is Severity.INFO
    assert findings[0].detector == "privacy_check_failed"
    assert "latextify inspect" in findings[0].message


# --------------------------------------------------------------------------- #
# run_preflight(): wiring into the real preflight pass
# --------------------------------------------------------------------------- #


def test_run_preflight_surfaces_privacy_findings(tmp_path):
    report = run_preflight(_tracked_and_commented_docx(tmp_path))
    detectors = {f.detector for f in report.findings}
    assert "privacy_tracked-changes" in detectors
    assert "privacy_comments" in detectors


def test_run_preflight_privacy_findings_never_block_conversion(tmp_path):
    """Comments alone (no unresolved tracked change) must never raise
    `has_errors` -- author names, comments, and the like are informational,
    not a reason to fail a conversion that would otherwise succeed."""
    report = run_preflight(_commented_only_docx(tmp_path))
    assert report.has_errors is False
    assert any(f.detector == "privacy_comments" for f in report.findings)


def test_run_preflight_tolerates_a_failing_privacy_inspection(monkeypatch):
    """A privacy-layer failure must degrade, never break run_preflight --
    a privacy nicety must not be able to fail a conversion that otherwise
    succeeds."""

    def _boom(_path):
        raise RuntimeError("simulated docx_adapter failure")

    monkeypatch.setattr(docx_adapter, "inspect", _boom)
    report = run_preflight(CLEAN_DOCX)  # its own structural parse still succeeds
    assert report.has_errors is False
    assert any(f.detector == "privacy_check_failed" for f in report.findings)


# --------------------------------------------------------------------------- #
# report.md: findings actually reach the rendered/written report
# --------------------------------------------------------------------------- #


def test_findings_reach_the_rendered_report(tmp_path):
    report = run_preflight(_tracked_and_commented_docx(tmp_path))
    text = render_report(preflight=report)

    assert "privacy_tracked-changes" in text
    assert "privacy_comments" in text
    # Document-level findings must render legibly, not as the sentinel value.
    assert "document-level" in text
    assert "¶-1" not in text


def test_findings_reach_the_written_report_file(tmp_path):
    report = run_preflight(_tracked_and_commented_docx(tmp_path))
    out = write_report(tmp_path / "report.md", preflight=report)
    text = out.read_text(encoding="utf-8")

    assert "privacy_tracked-changes" in text
    assert "privacy_comments" in text
