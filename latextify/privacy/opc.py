"""Shared Open Packaging Conventions (OPC) machinery.

``.docx``, ``.pptx`` and ``.xlsx`` are the same container: a ZIP holding
``[Content_Types].xml``, a ``_rels/`` tree, and ``docProps/*``. Only the body
vocabulary differs (WordprocessingML / PresentationML / SpreadsheetML), so
everything *package-level* -- which is the fiddly part -- belongs here once.

The fiddly part is not deleting a member; it is deleting a member without
corrupting the package. Office refuses to open a file whose
``[Content_Types].xml`` still declares an ``Override`` for a part that is gone,
or whose ``.rels`` still points at one. :func:`rewrite_package` therefore does
three things together, and callers should never do them separately:

    1. drop the requested parts, plus any ``.rels`` file left orphaned by them
    2. prune the matching ``Override`` entries from ``[Content_Types].xml``
    3. prune the matching ``Relationship`` entries from every surviving ``.rels``

This logic was proven in :mod:`latextify.ingest.docx_clean` (plan item 3) and
is extracted here unchanged in behaviour so the other OPC formats inherit it
rather than re-deriving it -- three near-copies of package bookkeeping is
exactly how one of them ends up subtly wrong.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path

from lxml import etree

from latextify.ingest._xml import hardened_xml_parser
from latextify.ingest.archive_guard import stream_zip_member, validate_docx_archive

from .report import Finding

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_EXT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"

RELS_MEMBER_RE = re.compile(r"^(?:(.*)/)?_rels/([^/]+)\.rels$")

#: Authoring-metadata parts every OPC format shares.
DOCPROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")

#: The saved first-page preview is a rendered image of the document -- a visual
#: content leak in a file that is otherwise "clean".
THUMBNAIL_RE = re.compile(r"^docProps/thumbnail\.[^/]+$")


def validate_opc_archive(path: Path | str, label: str) -> None:
    """Resource-safety check for any OPC package, reported as ``label``.

    Thin wrapper over the ingest archive guard so ``.pptx``/``.xlsx`` errors
    name their own format instead of ``.docx``.
    """
    validate_docx_archive(path, label=label)


# ── package part/rels path resolution ────────────────────────────────────


def owning_part(rels_member: str) -> str | None:
    """The part a ``.../_rels/NAME.rels`` file describes relationships FOR.

    ``None`` for the package-root ``_rels/.rels`` (not tied to one removable
    part) or any name not matching the ``.rels`` convention.
    """
    if rels_member == "_rels/.rels":
        return None
    match = RELS_MEMBER_RE.match(rels_member)
    if not match:
        return None
    dir_part, name = match.group(1) or "", match.group(2)
    return f"{dir_part}/{name}" if dir_part else name


def rels_base_dir(rels_member: str) -> str:
    """Directory a ``.rels`` file's relative ``Target`` values resolve against."""
    if rels_member == "_rels/.rels":
        return ""
    match = RELS_MEMBER_RE.match(rels_member)
    return (match.group(1) or "") if match else ""


def resolve_target(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    joined = posixpath.join(base_dir, target) if base_dir else target
    return posixpath.normpath(joined)


def scrub_rels(data: bytes, base_dir: str, parts_to_remove: set[str]) -> tuple[bytes, bool]:
    """Drop any ``<Relationship>`` whose ``Target`` resolves into ``parts_to_remove``."""
    root = etree.fromstring(data, parser=hardened_xml_parser())
    changed = False
    for rel in list(root):
        if not isinstance(rel.tag, str):
            continue
        if rel.get("TargetMode") == "External":
            continue  # not a package part; nothing to resolve/drop
        target = rel.get("Target")
        if target is None:
            continue
        if resolve_target(base_dir, target) in parts_to_remove:
            root.remove(rel)
            changed = True
    if not changed:
        return data, False
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), True


def scrub_content_types(data: bytes, parts_to_remove: set[str]) -> bytes:
    """Drop each removed part's ``<Override>`` entry from ``[Content_Types].xml``."""
    root = etree.fromstring(data, parser=hardened_xml_parser())
    wanted = {f"/{part}" for part in parts_to_remove}
    for override in root.findall(f"{{{CT_NS}}}Override"):
        if override.get("PartName") in wanted:
            root.remove(override)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def orphaned_rels(names: set[str], parts_to_remove: set[str]) -> set[str]:
    """``.rels`` members whose owning part is being removed."""
    orphans = set()
    for name in names:
        if not name.endswith(".rels"):
            continue
        owner = owning_part(name)
        if owner is not None and owner in parts_to_remove:
            orphans.add(name)
    return orphans


def rewrite_package(
    zin: zipfile.ZipFile,
    dest: Path,
    *,
    parts_to_remove: set[str],
    replacements: dict[str, bytes] | None = None,
) -> None:
    """Write ``zin`` to ``dest`` minus ``parts_to_remove``, package-consistent.

    ``replacements`` are body-part rewrites the caller already computed; this
    function adds the ``[Content_Types].xml`` and ``.rels`` bookkeeping so the
    result opens. Members are streamed rather than fully read, so a large deck
    does not land in memory.
    """
    names = set(zin.namelist())
    merged: dict[str, bytes] = dict(replacements or {})
    drop_members = set(parts_to_remove) | orphaned_rels(names, parts_to_remove)

    if parts_to_remove and "[Content_Types].xml" in names:
        merged["[Content_Types].xml"] = scrub_content_types(
            zin.read("[Content_Types].xml"), parts_to_remove
        )

    if parts_to_remove:
        for name in names:
            if name in drop_members or not name.endswith(".rels"):
                continue
            new_rels, changed = scrub_rels(zin.read(name), rels_base_dir(name), parts_to_remove)
            if changed:
                merged[name] = new_rels

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename in drop_members:
                continue
            replacement = merged.get(item.filename)
            if replacement is not None:
                zout.writestr(item.filename, replacement)
            else:
                stream_zip_member(zin, zout, item)


# ── docProps inspection (shared by every OPC format) ─────────────────────

#: core.xml fields worth naming individually, with why each matters.
_CORE_FIELDS = (
    (f"{{{_DC_NS}}}creator", "author", "high", "the document's original author"),
    (
        f"{{{_CP_NS}}}lastModifiedBy",
        "last-modified-by",
        "high",
        "who last saved the file -- often a reviewer the recipient should not see",
    ),
    (f"{{{_DC_NS}}}title", "title", "low", "an internal working title"),
    (f"{{{_DC_NS}}}subject", "subject", "low", "a subject line set by a template"),
    (f"{{{_DC_NS}}}description", "description", "medium", "free-text notes saved with the file"),
    (f"{{{_CP_NS}}}keywords", "keywords", "low", "keywords that can name an internal project"),
    (f"{{{_CP_NS}}}category", "category", "low", "a category set by a template"),
    (
        f"{{{_CP_NS}}}revision",
        "revision-count",
        "low",
        "how many times the file was saved -- reveals drafting history",
    ),
    (
        f"{{{_DCTERMS_NS}}}created",
        "created-date",
        "low",
        "when the document was first created",
    ),
    (
        f"{{{_DCTERMS_NS}}}modified",
        "modified-date",
        "low",
        "when the document was last saved",
    ),
)

#: app.xml fields. TotalTime is the notorious one.
_APP_FIELDS = (
    (
        "Company",
        "company",
        "high",
        "the organization licensed to the installed Office copy",
    ),
    ("Manager", "manager", "medium", "a manager name saved by Office"),
    (
        "Template",
        "template",
        "medium",
        "the template the file was built from -- can name an internal or a journal-specific path",
    ),
    (
        "TotalTime",
        "editing-time",
        "medium",
        "total minutes spent editing -- reveals how long the work actually took",
    ),
    (
        "LastModifiedBy",
        "last-modified-by",
        "high",
        "who last saved the file",
    ),
)


def _text_of(root: etree._Element, tag: str) -> str:
    """First non-empty value for ``tag``.

    Office normally writes each property once, but it also writes *empty*
    placeholder elements (``<Company></Company>``), and a file that has been
    edited by more than one tool can carry both an empty placeholder and a
    populated duplicate. Taking the first non-empty match rather than simply
    the first match means a real value is never masked by a placeholder that
    happens to precede it.
    """
    for node in root.findall(tag):
        if node.text and node.text.strip():
            return node.text.strip()
    return ""


def docprops_findings(zin: zipfile.ZipFile) -> list[Finding]:
    """Inspect ``docProps/*`` and the saved thumbnail across any OPC format."""
    names = set(zin.namelist())
    findings: list[Finding] = []

    if "docProps/core.xml" in names:
        findings.extend(_core_findings(zin.read("docProps/core.xml")))
    if "docProps/app.xml" in names:
        findings.extend(_app_findings(zin.read("docProps/app.xml")))
    if "docProps/custom.xml" in names:
        findings.extend(_custom_findings(zin.read("docProps/custom.xml")))

    thumbnails = sorted(name for name in names if THUMBNAIL_RE.match(name))
    if thumbnails:
        findings.append(
            Finding(
                category="thumbnail",
                severity="medium",
                summary="Saved first-page preview image",
                detail=(
                    "A rendered picture of page one is stored in the file. It survives "
                    "edits to the text, so it can show content that was later changed "
                    "or removed."
                ),
                location=thumbnails[0],
                count=len(thumbnails),
            )
        )
    return findings


def _parse(data: bytes) -> etree._Element | None:
    try:
        return etree.fromstring(data, parser=hardened_xml_parser())
    except etree.XMLSyntaxError:
        return None


def _core_findings(data: bytes) -> list[Finding]:
    root = _parse(data)
    if root is None:
        return []
    findings = []
    for tag, category, severity, why in _CORE_FIELDS:
        value = _text_of(root, tag)
        if value:
            findings.append(
                Finding(
                    category=category,
                    severity=severity,
                    summary=f"{category.replace('-', ' ').capitalize()}: {value}",
                    detail=f"docProps/core.xml records {why}.",
                    location="docProps/core.xml",
                )
            )
    return findings


def _app_findings(data: bytes) -> list[Finding]:
    root = _parse(data)
    if root is None:
        return []
    findings = []
    for name, category, severity, why in _APP_FIELDS:
        value = _text_of(root, f"{{{_EXT_NS}}}{name}")
        if value and value != "0":
            findings.append(
                Finding(
                    category=category,
                    severity=severity,
                    summary=f"{name}: {value}",
                    detail=f"docProps/app.xml records {why}.",
                    location="docProps/app.xml",
                )
            )
    return findings


def _custom_findings(data: bytes) -> list[Finding]:
    root = _parse(data)
    if root is None:
        return []
    props = root.findall(f"{{{_CUSTOM_NS}}}property")
    if not props:
        return []
    named = [p.get("name", "") for p in props if p.get("name")]
    shown = ", ".join(named[:5]) + (", ..." if len(named) > 5 else "")
    return [
        Finding(
            category="custom-properties",
            severity="medium",
            summary=f"{len(props)} custom document propert{'y' if len(props) == 1 else 'ies'}"
            + (f" ({shown})" if shown else ""),
            detail=(
                "Custom properties are set by templates, document-management systems "
                "and add-ins. They routinely carry internal project codes, matter "
                "numbers, classification labels and original author names."
            ),
            location="docProps/custom.xml",
            count=len(props),
        )
    ]


def docprops_parts_present(names: set[str]) -> set[str]:
    """The docProps parts (and thumbnail) actually present in a package."""
    present = {name for name in DOCPROPS_PARTS if name in names}
    present |= {name for name in names if THUMBNAIL_RE.match(name)}
    return present
