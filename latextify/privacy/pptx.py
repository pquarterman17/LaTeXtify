"""PowerPoint (.pptx) inspection and sanitizing.

PresentationML rides the same OPC package as WordprocessingML, so
``docProps`` handling comes free from :mod:`.opc`. What is specific to decks
is the set of places content hides *in plain sight*:

- **Embedded chart workbooks** (``ppt/embeddings/*.xlsx``). A chart that shows
  three summary bars carries the entire worksheet it was built from, because
  PowerPoint stores the source data so the chart stays editable. This is the
  single most under-appreciated leak in the format: the visible chart is a
  summary, the embedded file is the raw data.
- **Speaker notes** (``ppt/notesSlides/*``). Invisible in the delivered deck,
  fully present in the file, and where candid remarks live.
- **Hidden slides** (``<p:sld show="0">``). Cut from the talk, still shipped.
- **Comments** and their author list.
- **Off-canvas shapes** -- content dragged just outside the slide edge to get
  it "out of the way". It never renders and is never noticed again.

Removal of a slide is real surgery: the slide part, its ``.rels``, its
``p:sldId`` entry in ``ppt/presentation.xml``, the relationship pointing at it,
and its ``[Content_Types].xml`` override must all go together.
:func:`~latextify.privacy.opc.rewrite_package` handles the last three; this
module supplies the first and the ``presentation.xml`` edit.

Off-canvas shapes are **reported, not removed**: deleting a shape is a content
edit whose blast radius we cannot bound (it may be a deliberate bleed element),
so the honest move is to name it and let the author decide.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from latextify.ingest._xml import hardened_xml_parser

from . import opc
from .report import Finding

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"

_PRESENTATION_PART = "ppt/presentation.xml"
_SLIDE_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
_NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide\d+\.xml$")
_COMMENTS_RE = re.compile(r"^ppt/comments/[^/]+\.xml$")
_AUTHORS_PART = "ppt/commentAuthors.xml"
_EMBEDDING_RE = re.compile(r"^ppt/embeddings/[^/]+$")
_CHART_RE = re.compile(r"^ppt/charts/chart\d+\.xml$")


def _parse(data: bytes) -> etree._Element | None:
    try:
        return etree.fromstring(data, parser=hardened_xml_parser())
    except etree.XMLSyntaxError:
        return None


def _serialize(root: etree._Element) -> bytes:
    xml: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return xml


def _notes_text(data: bytes) -> str:
    """Visible text of a notes slide, minus the auto-inserted slide number."""
    root = _parse(data)
    if root is None:
        return ""
    chunks = [node.text or "" for node in root.iter(f"{{{A}}}t")]
    return " ".join(" ".join(chunks).split())


def _slide_size(zin: zipfile.ZipFile) -> tuple[int, int] | None:
    if _PRESENTATION_PART not in set(zin.namelist()):
        return None
    root = _parse(zin.read(_PRESENTATION_PART))
    if root is None:
        return None
    sz = root.find(f"{{{P}}}sldSz")
    if sz is None:
        return None
    try:
        return int(sz.get("cx", "0")), int(sz.get("cy", "0"))
    except ValueError:
        return None


def _hidden_slides(zin: zipfile.ZipFile, names: set[str]) -> list[str]:
    """Slide parts whose root carries ``show="0"``."""
    hidden = []
    for name in sorted(n for n in names if _SLIDE_RE.match(n)):
        root = _parse(zin.read(name))
        if root is not None and root.get("show") == "0":
            hidden.append(name)
    return hidden


def _offcanvas_shapes(zin: zipfile.ZipFile, names: set[str]) -> list[tuple[str, str]]:
    """(slide, shape name) pairs positioned wholly outside the slide bounds."""
    size = _slide_size(zin)
    if size is None:
        return []
    width, height = size
    if width <= 0 or height <= 0:
        return []

    found: list[tuple[str, str]] = []
    for name in sorted(n for n in names if _SLIDE_RE.match(n)):
        root = _parse(zin.read(name))
        if root is None:
            continue
        for xfrm in root.iter(f"{{{A}}}xfrm"):
            off = xfrm.find(f"{{{A}}}off")
            ext = xfrm.find(f"{{{A}}}ext")
            if off is None or ext is None:
                continue
            try:
                x, y = int(off.get("x", "0")), int(off.get("y", "0"))
                cx, cy = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
            except ValueError:
                continue
            # Wholly outside: the shape's own box never intersects the slide.
            if x + cx <= 0 or y + cy <= 0 or x >= width or y >= height:
                found.append((name, _shape_label(xfrm)))
    return found


def _shape_label(xfrm: etree._Element) -> str:
    """Best-effort human name for the shape owning ``xfrm``."""
    node: etree._Element | None = xfrm
    while node is not None:
        nv = node.find(f".//{{{P}}}cNvPr")
        if nv is not None and nv.get("name"):
            name: str = nv.get("name", "")
            return name
        node = node.getparent()
    return "unnamed shape"


def _embedded_workbooks(names: set[str]) -> list[str]:
    return sorted(n for n in names if _EMBEDDING_RE.match(n))


def _comment_parts(names: set[str]) -> list[str]:
    parts = sorted(n for n in names if _COMMENTS_RE.match(n))
    if _AUTHORS_PART in names:
        parts.append(_AUTHORS_PART)
    return parts


def _notes_parts_with_text(zin: zipfile.ZipFile, names: set[str]) -> list[tuple[str, str]]:
    out = []
    for name in sorted(n for n in names if _NOTES_RE.match(n)):
        text = _notes_text(zin.read(name))
        if text:
            out.append((name, text))
    return out


# ── inspection ───────────────────────────────────────────────────────────


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report what a ``.pptx`` carries, without modifying it."""
    opc.validate_opc_archive(path, ".pptx")
    findings: list[Finding] = []
    warnings: list[str] = []

    with zipfile.ZipFile(path) as zin:
        names = set(zin.namelist())
        if _PRESENTATION_PART not in names:
            raise ValueError(f"{path}: not a valid .pptx (missing {_PRESENTATION_PART})")

        findings.extend(opc.docprops_findings(zin))

        workbooks = _embedded_workbooks(names)
        if workbooks:
            findings.append(
                Finding(
                    category="embedded-workbook",
                    severity="high",
                    summary=f"{len(workbooks)} embedded workbook/object file(s)",
                    detail=(
                        "Charts store the worksheet they were built from so the chart "
                        "stays editable. The visible chart may show only a summary "
                        "while the embedded file carries every underlying row."
                    ),
                    location=workbooks[0],
                    count=len(workbooks),
                )
            )

        notes = _notes_parts_with_text(zin, names)
        if notes:
            findings.append(
                Finding(
                    category="speaker-notes",
                    severity="high",
                    summary=f"Speaker notes on {len(notes)} slide(s)",
                    detail=(
                        "Notes never appear in the delivered deck but are stored in "
                        "full. Example: "
                        f"{notes[0][1][:80]!r}"
                    ),
                    location=notes[0][0],
                    count=len(notes),
                )
            )

        hidden = _hidden_slides(zin, names)
        if hidden:
            findings.append(
                Finding(
                    category="hidden-slide",
                    severity="high",
                    summary=f"{len(hidden)} hidden slide(s)",
                    detail=(
                        "Slides marked hidden are skipped during presentation but "
                        "ship with the file and open normally in edit view."
                    ),
                    location=hidden[0],
                    count=len(hidden),
                )
            )

        comments = _comment_parts(names)
        if comments:
            findings.append(
                Finding(
                    category="comments",
                    severity="medium",
                    summary=f"{len(comments)} comment/author part(s)",
                    detail=(
                        "Review comments and the list of commenter names are stored in the deck."
                    ),
                    location=comments[0],
                    count=len(comments),
                )
            )

        offcanvas = _offcanvas_shapes(zin, names)
        if offcanvas:
            slides = sorted({slide for slide, _ in offcanvas})
            findings.append(
                Finding(
                    category="off-canvas",
                    severity="medium",
                    summary=f"{len(offcanvas)} shape(s) positioned off-slide",
                    detail=(
                        "Content dragged outside the slide area never renders but is "
                        "fully present in the file. Reported only -- removing a shape "
                        "is a content edit, so it is left for you to decide."
                    ),
                    location=slides[0],
                    count=len(offcanvas),
                    removable=False,
                )
            )
            warnings.append(
                f"{len(offcanvas)} off-slide shape(s) on {len(slides)} slide(s) were "
                "found but NOT removed -- open the deck and delete them if they are "
                "leftovers."
            )

    return findings, warnings


# ── sanitizing ───────────────────────────────────────────────────────────


def sanitize(
    src: Path, dest: Path, *, keep_notes: bool = False, **_options: object
) -> tuple[list[Finding], list[str]]:
    """Write a scrubbed copy of ``src`` to ``dest``.

    Removes docProps, embedded chart workbooks (and the chart's reference to
    them), comments, hidden slides, and -- unless ``keep_notes`` -- speaker
    notes. Off-canvas shapes are reported as warnings, never deleted.
    """
    opc.validate_opc_archive(src, ".pptx")
    removed: list[Finding] = []
    warnings: list[str] = []

    with zipfile.ZipFile(src) as zin:
        names = set(zin.namelist())
        if _PRESENTATION_PART not in names:
            raise ValueError(f"{src}: not a valid .pptx (missing {_PRESENTATION_PART})")

        parts_to_remove: set[str] = set()
        replacements: dict[str, bytes] = {}

        docprops = opc.docprops_parts_present(names)
        if docprops:
            parts_to_remove |= docprops
            removed.append(
                Finding(
                    category="docprops",
                    severity="high",
                    summary=f"Stripped {len(docprops)} document-properties part(s)",
                    detail="Author, company, editing time, custom properties and the "
                    "saved preview image were removed.",
                    location="docProps/",
                    count=len(docprops),
                )
            )

        workbooks = _embedded_workbooks(names)
        if workbooks:
            parts_to_remove |= set(workbooks)
            removed.append(
                Finding(
                    category="embedded-workbook",
                    severity="high",
                    summary=f"Removed {len(workbooks)} embedded workbook/object file(s)",
                    detail="The source data behind charts and embedded objects is gone; "
                    "charts still render from their cached values.",
                    location=workbooks[0],
                    count=len(workbooks),
                )
            )
            for chart in sorted(n for n in names if _CHART_RE.match(n)):
                stripped = _strip_external_data(zin.read(chart))
                if stripped is not None:
                    replacements[chart] = stripped

        comments = _comment_parts(names)
        if comments:
            parts_to_remove |= set(comments)
            removed.append(
                Finding(
                    category="comments",
                    severity="medium",
                    summary=f"Removed {len(comments)} comment/author part(s)",
                    detail="Review comments and commenter names were deleted.",
                    location=comments[0],
                    count=len(comments),
                )
            )

        if not keep_notes:
            notes = _notes_parts_with_text(zin, names)
            if notes:
                all_notes = {n for n in names if _NOTES_RE.match(n)}
                parts_to_remove |= all_notes
                removed.append(
                    Finding(
                        category="speaker-notes",
                        severity="high",
                        summary=f"Removed speaker notes from {len(notes)} slide(s)",
                        detail="Notes parts were deleted from the package.",
                        location=notes[0][0],
                        count=len(notes),
                    )
                )

        hidden = _hidden_slides(zin, names)
        if hidden:
            parts_to_remove |= set(hidden)
            # Slides also need their notes part and the presentation.xml entry.
            parts_to_remove |= _notes_for_slides(zin, names, hidden)
            new_presentation = _drop_slide_entries(zin, hidden)
            if new_presentation is not None:
                replacements[_PRESENTATION_PART] = new_presentation
            removed.append(
                Finding(
                    category="hidden-slide",
                    severity="high",
                    summary=f"Removed {len(hidden)} hidden slide(s)",
                    detail="Slides marked hidden were deleted along with their notes "
                    "and their entry in the slide list.",
                    location=hidden[0],
                    count=len(hidden),
                )
            )

        offcanvas = _offcanvas_shapes(zin, names)
        if offcanvas:
            slides = sorted({slide for slide, _ in offcanvas})
            warnings.append(
                f"{len(offcanvas)} off-slide shape(s) on {len(slides)} slide(s) were "
                "found but NOT removed -- they are still in the cleaned file."
            )

        opc.rewrite_package(zin, dest, parts_to_remove=parts_to_remove, replacements=replacements)

    return removed, warnings


def _strip_external_data(data: bytes) -> bytes | None:
    """Drop ``c:externalData`` (the chart's link to its embedded workbook)."""
    root = _parse(data)
    if root is None:
        return None
    nodes = list(root.iter(f"{{{C}}}externalData"))
    if not nodes:
        return None
    for node in nodes:
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    return _serialize(root)


def _rels_for(part: str) -> str:
    head, _, tail = part.rpartition("/")
    return f"{head}/_rels/{tail}.rels" if head else f"_rels/{tail}.rels"


def _notes_for_slides(zin: zipfile.ZipFile, names: set[str], slides: list[str]) -> set[str]:
    """Notes parts belonging to ``slides`` (so a removed slide leaves none behind)."""
    notes: set[str] = set()
    for slide in slides:
        rels_name = _rels_for(slide)
        if rels_name not in names:
            continue
        root = _parse(zin.read(rels_name))
        if root is None:
            continue
        base = opc.rels_base_dir(rels_name)
        for rel in root:
            if not isinstance(rel.tag, str) or rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target")
            if target is None:
                continue
            resolved = opc.resolve_target(base, target)
            if _NOTES_RE.match(resolved):
                notes.add(resolved)
    return notes


def _drop_slide_entries(zin: zipfile.ZipFile, slides: list[str]) -> bytes | None:
    """Remove removed slides' ``p:sldId`` entries from ``ppt/presentation.xml``."""
    rels_name = _rels_for(_PRESENTATION_PART)
    if rels_name not in set(zin.namelist()):
        return None
    rels_root = _parse(zin.read(rels_name))
    presentation = _parse(zin.read(_PRESENTATION_PART))
    if rels_root is None or presentation is None:
        return None

    base = opc.rels_base_dir(rels_name)
    doomed_ids = {
        rel.get("Id")
        for rel in rels_root
        if isinstance(rel.tag, str)
        and rel.get("TargetMode") != "External"
        and rel.get("Target") is not None
        and opc.resolve_target(base, rel.get("Target", "")) in set(slides)
    }
    if not doomed_ids:
        return None

    id_attr = f"{{{R}}}id"
    changed = False
    for lst in presentation.findall(f"{{{P}}}sldIdLst"):
        for entry in list(lst):
            if entry.get(id_attr) in doomed_ids:
                lst.remove(entry)
                changed = True
    return _serialize(presentation) if changed else None
