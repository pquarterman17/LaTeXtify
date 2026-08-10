"""Bridge the existing ``.docx`` sanitizer into the privacy registry.

:func:`latextify.ingest.docx_clean.sanitize_docx` predates this package, is
well covered by tests, and is wired into the GUI schema via its own
``CleanReport``. Rather than rewrite it -- the surest way to regress a
security-relevant path -- this module wraps it: sanitizing delegates verbatim
and translates the result into :class:`~latextify.privacy.report.Finding`
objects, while inspection is implemented here because ``docx_clean`` only ever
offered a destructive path.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from latextify.ingest._xml import hardened_xml_parser
from latextify.ingest.docx_clean import sanitize_docx

from . import opc
from .report import Finding

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_WORDML_PART_RE = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
_COMMENTS_PART_RE = re.compile(r"^word/comments[^/]*\.xml$")
_DOCUMENT_PART = "word/document.xml"


def _q(localname: str) -> str:
    return f"{{{W}}}{localname}"


def _parse(data: bytes) -> etree._Element | None:
    try:
        return etree.fromstring(data, parser=hardened_xml_parser())
    except etree.XMLSyntaxError:
        return None


def _ooxml_bool(value: str | None) -> bool:
    return value is None or value not in ("0", "false", "off")


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report what a ``.docx`` carries, without modifying it."""
    opc.validate_opc_archive(path, ".docx")
    findings: list[Finding] = []

    with zipfile.ZipFile(path) as zin:
        names = set(zin.namelist())
        if _DOCUMENT_PART not in names:
            raise ValueError(f"{path}: not a valid .docx (missing {_DOCUMENT_PART})")

        findings.extend(opc.docprops_findings(zin))

        tracked = 0
        hidden = 0
        for name in sorted(n for n in names if _WORDML_PART_RE.match(n)):
            root = _parse(zin.read(name))
            if root is None:
                continue
            tracked += len(root.findall(f".//{_q('ins')}")) + len(root.findall(f".//{_q('del')}"))
            for run in root.findall(f".//{_q('r')}"):
                rpr = run.find(_q("rPr"))
                if rpr is None:
                    continue
                vanish = rpr.find(_q("vanish"))
                if vanish is not None and _ooxml_bool(vanish.get(_q("val"))):
                    hidden += 1

        if tracked:
            findings.append(
                Finding(
                    category="tracked-changes",
                    severity="high",
                    summary=f"{tracked} tracked change(s)",
                    detail=(
                        "Insertions and deletions record what was edited and, with the "
                        "reviewer list, by whom. Deleted text is still readable in the file."
                    ),
                    location=_DOCUMENT_PART,
                    count=tracked,
                )
            )

        if hidden:
            findings.append(
                Finding(
                    category="hidden-text",
                    severity="high",
                    summary=f"{hidden} hidden text run(s)",
                    detail=(
                        "Text formatted as hidden does not display or print but is "
                        "stored in full and reappears if hidden text is switched on."
                    ),
                    location=_DOCUMENT_PART,
                    count=hidden,
                )
            )

        comment_parts = sorted(n for n in names if _COMMENTS_PART_RE.match(n))
        if comment_parts:
            count = 0
            if "word/comments.xml" in names:
                root = _parse(zin.read("word/comments.xml"))
                if root is not None:
                    count = len(root.findall(_q("comment")))
            findings.append(
                Finding(
                    category="comments",
                    severity="medium",
                    summary=f"{count or len(comment_parts)} review comment(s)",
                    detail="Comment text and the names of the people who wrote them "
                    "are stored in the document.",
                    location=comment_parts[0],
                    count=max(count, 1),
                )
            )

        if "word/people.xml" in names:
            findings.append(
                Finding(
                    category="reviewers",
                    severity="high",
                    summary="Cached reviewer identity list",
                    detail="word/people.xml caches reviewer names and presence "
                    "information written by some Word versions.",
                    location="word/people.xml",
                )
            )

        if "word/settings.xml" in names:
            root = _parse(zin.read("word/settings.xml"))
            if root is not None and root.find(_q("rsids")) is not None:
                findings.append(
                    Finding(
                        category="rsids",
                        severity="low",
                        summary="Revision-session identifiers present",
                        detail="The rsid index fingerprints editing sessions and can "
                        "link separate documents to the same author's Word install.",
                        location="word/settings.xml",
                    )
                )

    return findings, []


def sanitize(src: Path, dest: Path, **_options: object) -> tuple[list[Finding], list[str]]:
    """Delegate to the existing sanitizer, translating its report."""
    report = sanitize_docx(src, dest)
    removed: list[Finding] = []

    if report.docprops_stripped:
        removed.append(
            Finding(
                category="docprops",
                severity="high",
                summary="Stripped document properties",
                detail="Author, company, editing time, custom properties and the saved "
                "preview image were removed.",
                location="docProps/",
            )
        )
    if report.tracked_changes_accepted:
        removed.append(
            Finding(
                category="tracked-changes",
                severity="high",
                summary=f"Accepted {report.tracked_changes_accepted} tracked change(s)",
                detail="Insertions were kept as normal text and deletions were dropped, "
                "so the edit history is gone.",
                location="word/document.xml",
                count=report.tracked_changes_accepted,
            )
        )
    if report.comments_removed:
        removed.append(
            Finding(
                category="comments",
                severity="medium",
                summary=f"Removed {report.comments_removed} review comment(s)",
                detail="Comment text and commenter names were deleted.",
                location="word/comments.xml",
                count=report.comments_removed,
            )
        )
    if report.hidden_runs_removed:
        removed.append(
            Finding(
                category="hidden-text",
                severity="high",
                summary=f"Removed {report.hidden_runs_removed} hidden text run(s)",
                detail="Hidden runs were deleted, not merely un-hidden.",
                location="word/document.xml",
                count=report.hidden_runs_removed,
            )
        )
    if report.rsids_scrubbed:
        removed.append(
            Finding(
                category="rsids",
                severity="low",
                summary="Scrubbed revision-session identifiers",
                detail="The settings.xml rsid index was removed.",
                location="word/settings.xml",
            )
        )

    warnings = [
        "Row/cell-level tracked changes and paragraph-mark merges are approximated; "
        "scattered rsid attributes in the body are left in place."
    ]
    return removed, warnings
