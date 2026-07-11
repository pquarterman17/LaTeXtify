"""Preflight: inventory a .docx and flag constructs pandoc will mangle.

A .docx is a ZIP archive. This module reads two members directly with lxml
(no pandoc involved yet):

    word/document.xml  -- body content: paragraphs, runs, field codes, drawings
    word/styles.xml     -- style definitions (headings, Caption, Title)

`run_preflight()` is the entry point: it returns a
`latextify.model.preflight.PreflightReport` combining every detector's
findings with a style-usage inventory.

Detectors (see plan item 2 for the construct -> XML-tag mapping):
    text boxes         -- w:txbxContent anywhere in the document
    tracked changes     -- w:ins / w:del
    floating objects    -- wp:anchor inside a w:drawing (as opposed to wp:inline)
    SmartArt            -- a:graphicData whose uri is the DrawingML diagram namespace
    equation-as-image   -- a paragraph containing a w:drawing whose own text
                           starts with "Eq" / "Equation" (heuristic; WARN not ERROR)
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from latextify.model.preflight import (
    Location,
    PreflightFinding,
    PreflightReport,
    Severity,
    StyleInventory,
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}

_DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_EQUATION_TEXT_RE = re.compile(r"^\s*(Eq|Equation)\b", re.IGNORECASE)
_SNIPPET_MAX_LEN = 80


def _qn(prefixed_tag: str) -> str:
    """Expand a `prefix:local` tag name into lxml's Clark notation."""
    prefix, local = prefixed_tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def _read_member_xml(docx_path: str | Path, member: str) -> etree._Element | None:
    """Parse one XML member out of the .docx ZIP; None if it doesn't exist."""
    try:
        archive = zipfile.ZipFile(docx_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{docx_path}: not a valid .docx ({exc})") from exc
    with archive:
        if member not in archive.namelist():
            return None
        with archive.open(member) as fh:
            try:
                return etree.parse(fh).getroot()
            except etree.XMLSyntaxError as exc:
                raise ValueError(
                    f"{docx_path}: not a valid .docx (malformed XML in {member}: {exc})"
                ) from exc


def _paragraph_text(paragraph: etree._Element) -> str:
    """Concatenate all visible text runs (w:t) under a paragraph."""
    texts = paragraph.findall(f".//{_qn('w:t')}")
    return "".join(t.text or "" for t in texts)


def _snippet(text: str, max_len: int = _SNIPPET_MAX_LEN) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


class ParagraphIndex:
    """Document-order paragraph lookup, keyed by element identity.

    lxml hands out a fresh Python proxy object (with a new `id()`) for an
    element once every previous proxy referencing it has been garbage
    collected, so an `id() -> index` map is only valid while *something*
    keeps the original proxies alive. This class holds the paragraph list
    itself for exactly that reason -- do not let an instance be dropped
    before every lookup against it has been made.
    """

    def __init__(self, paragraphs: list[etree._Element]) -> None:
        self._paragraphs = paragraphs  # keeps proxies alive; see docstring
        self._by_id = {id(p): i for i, p in enumerate(paragraphs)}

    def index_of(self, paragraph: etree._Element) -> int | None:
        return self._by_id.get(id(paragraph))


def _build_paragraph_index(document_root: etree._Element) -> ParagraphIndex:
    """All w:p elements in document order, for location lookups."""
    return ParagraphIndex(document_root.findall(f".//{_qn('w:p')}"))


def _enclosing_paragraph(element: etree._Element) -> etree._Element | None:
    """Nearest ancestor w:p of `element`, or the element itself if it is one."""
    if element.tag == _qn("w:p"):
        return element
    for ancestor in element.iterancestors(_qn("w:p")):
        return ancestor
    return None


def _location_for(element: etree._Element, para_index: ParagraphIndex) -> Location | None:
    paragraph = _enclosing_paragraph(element)
    if paragraph is None:
        return None
    index = para_index.index_of(paragraph)
    if index is None:
        return None
    return Location(paragraph_index=index, text_snippet=_snippet(_paragraph_text(paragraph)))


def _finding(
    detector: str,
    severity: Severity,
    element: etree._Element,
    para_index: ParagraphIndex,
    message: str,
) -> PreflightFinding | None:
    location = _location_for(element, para_index)
    if location is None:
        return None
    return PreflightFinding(
        severity=severity, detector=detector, location=location, message=message
    )


def detect_text_boxes(
    document_root: etree._Element, para_index: ParagraphIndex
) -> list[PreflightFinding]:
    """Text box content (w:txbxContent) is invisible to pandoc's docx reader."""
    findings = []
    for element in document_root.iter(_qn("w:txbxContent")):
        finding = _finding(
            "text_box",
            Severity.ERROR,
            element,
            para_index,
            "Text box content will be dropped by conversion; move it into the "
            "main body flow before converting.",
        )
        if finding is not None:
            findings.append(finding)
    return findings


def detect_tracked_changes(
    document_root: etree._Element, para_index: ParagraphIndex
) -> list[PreflightFinding]:
    """Unresolved tracked insertions/deletions (w:ins / w:del)."""
    findings = []
    for tag, verb in ((_qn("w:ins"), "insertion"), (_qn("w:del"), "deletion")):
        for element in document_root.iter(tag):
            finding = _finding(
                "tracked_changes",
                Severity.ERROR,
                element,
                para_index,
                f"Unresolved tracked {verb}; accept or reject all changes before "
                "converting so the wrong text isn't carried into the manuscript.",
            )
            if finding is not None:
                findings.append(finding)
    return findings


def detect_floating_objects(
    document_root: etree._Element, para_index: ParagraphIndex
) -> list[PreflightFinding]:
    """Anchored (floating) drawings (wp:anchor) lose their text-relative position."""
    findings = []
    for element in document_root.iter(_qn("wp:anchor")):
        finding = _finding(
            "floating_object",
            Severity.WARN,
            element,
            para_index,
            "Floating (anchored) image; its position relative to the text is not "
            "preserved by conversion. Verify figure placement after conversion.",
        )
        if finding is not None:
            findings.append(finding)
    return findings


def detect_smartart(
    document_root: etree._Element, para_index: ParagraphIndex
) -> list[PreflightFinding]:
    """SmartArt diagrams (a:graphicData with the DrawingML diagram uri)."""
    findings = []
    for element in document_root.iter(_qn("a:graphicData")):
        if element.get("uri") == _DIAGRAM_URI:
            finding = _finding(
                "smartart",
                Severity.ERROR,
                element,
                para_index,
                "SmartArt diagram; its content cannot be converted and will be "
                "lost. Replace it with a static image or redraw it as a figure.",
            )
            if finding is not None:
                findings.append(finding)
    return findings


def detect_equation_as_image(
    document_root: etree._Element, para_index: ParagraphIndex
) -> list[PreflightFinding]:
    """Paragraphs that look like a labeled equation but embed an image."""
    findings = []
    for paragraph in document_root.findall(f".//{_qn('w:p')}"):
        if paragraph.find(f".//{_qn('w:drawing')}") is None:
            continue
        text = _paragraph_text(paragraph)
        if not _EQUATION_TEXT_RE.match(text):
            continue
        finding = _finding(
            "equation_as_image",
            Severity.WARN,
            paragraph,
            para_index,
            "Paragraph reads like an equation but contains an embedded image; "
            "verify this is a pasted screenshot rather than an equation-editor "
            "object, which would convert far better.",
        )
        if finding is not None:
            findings.append(finding)
    return findings


_DETECTORS = (
    detect_text_boxes,
    detect_tracked_changes,
    detect_floating_objects,
    detect_smartart,
    detect_equation_as_image,
)


_HEADING_NAME_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)


def _style_name_map(styles_root: etree._Element | None) -> dict[str, str]:
    """styleId -> lowercased style name, from word/styles.xml."""
    if styles_root is None:
        return {}
    mapping: dict[str, str] = {}
    for style in styles_root.findall(_qn("w:style")):
        style_id = style.get(_qn("w:styleId"))
        name_el = style.find(_qn("w:name"))
        if style_id is None or name_el is None:
            continue
        name = name_el.get(_qn("w:val")) or ""
        mapping[style_id] = name.strip().lower()
    return mapping


def build_style_inventory(
    document_root: etree._Element, styles_root: etree._Element | None
) -> StyleInventory:
    """Which heading levels, Title style, and Caption style are actually used."""
    style_names = _style_name_map(styles_root)
    heading_levels: set[int] = set()
    title_used = False
    caption_used = False

    for paragraph in document_root.findall(f".//{_qn('w:p')}"):
        p_style = paragraph.find(f"{_qn('w:pPr')}/{_qn('w:pStyle')}")
        if p_style is None:
            continue
        style_id = p_style.get(_qn("w:val"))
        if style_id is None:
            continue
        name = style_names.get(style_id, style_id.lower())

        heading_match = _HEADING_NAME_RE.match(name)
        if heading_match:
            heading_levels.add(int(heading_match.group(1)))
        elif name == "title":
            title_used = True
        elif name == "caption":
            caption_used = True

    return StyleInventory(
        heading_levels_used=frozenset(heading_levels),
        title_style_used=title_used,
        caption_style_used=caption_used,
    )


def run_preflight(docx_path: str | Path) -> PreflightReport:
    """Open `docx_path` and run every detector, returning the full report."""
    document_root = _read_member_xml(docx_path, "word/document.xml")
    if document_root is None:
        raise ValueError(f"{docx_path}: not a valid .docx (missing word/document.xml)")
    styles_root = _read_member_xml(docx_path, "word/styles.xml")

    para_index = _build_paragraph_index(document_root)
    findings: list[PreflightFinding] = []
    for detector in _DETECTORS:
        findings.extend(detector(document_root, para_index))

    styles = build_style_inventory(document_root, styles_root)
    return PreflightReport(findings=tuple(findings), styles=styles)
