"""Metadata-stripped clean-.docx export (plan item 3, FORMATS_AND_PRIVACY).

A headless "Document Inspector": :func:`sanitize_docx` writes a copy of a
source ``.docx`` with authoring metadata and hidden/review content removed,
while leaving the visible body content otherwise intact. Unlike the other
``ingest`` docx-rewrite helpers (:mod:`.citation_sentinels`,
:mod:`.frontmatter`), which only ever overwrite ``word/document.xml`` in
place, this pass also DROPS whole archive members (``docProps/*``,
``word/comments*.xml``, ``word/people.xml``) -- so it carries its own
streaming rewrite that additionally supports excluding members, and keeps
``[Content_Types].xml`` and every ``*.rels`` part consistent with whatever it
dropped (an orphaned ``Override``/``Relationship`` entry pointing at a
missing part is exactly the kind of corruption that makes Word refuse to
open an otherwise-valid package).

What gets scrubbed, and how "accept all changes" is approximated:
    - ``docProps/core.xml``, ``docProps/app.xml``, ``docProps/custom.xml``
      (author, company, edit time, custom properties, ...) are deleted
      outright, along with their ``[Content_Types].xml`` Override and
      ``_rels/.rels`` Relationship entries.
    - ``w:ins`` (tracked insertion) is unwrapped in place: its children
      (the inserted content) move up to replace it, keeping the text but
      dropping the "this was inserted" marker. This also correctly resolves
      the empty paragraph-mark-insertion marker
      (``w:pPr/w:rPr/w:ins``, no children) to a no-op removal.
    - ``w:del`` (tracked deletion, whose runs carry ``w:delText`` instead of
      ``w:t``) is removed wholesale -- deleted content never makes it into
      the sanitized copy. Any stray ``w:delText`` left outside a ``w:del``
      wrapper (not valid per schema, but defensive) is stripped too.
      Deletions are processed before insertions are unwrapped so that a
      ``w:del`` nested inside a ``w:ins`` (inserted, then later deleted,
      before either was accepted) resolves correctly: the deletion wins.
    - Any element whose local name ends in ``Change`` (``w:rPrChange``,
      ``w:pPrChange``, ``w:tblPrChange``, ``w:tcPrChange``, ``w:trPrChange``,
      ``w:tblGridChange``, ``w:sectPrChange``, ``w:numberingChange``, ...) is
      a cached "what the formatting/structure used to look like" snapshot;
      it is dropped outright since the current (post-accept) state is
      already what the surrounding element expresses.
    - ``word/comments.xml`` and any sibling comments part
      (``commentsExtended.xml``, ``commentsIds.xml``, ``commentsExtensible.xml``,
      ...) are deleted, together with the in-body anchor markers
      (``w:commentRangeStart``/``End``, ``w:commentReference``) and each
      comments part's own ``.rels`` file (now orphaned).
    - A run with an active ``w:vanish`` (``<w:vanish/>`` or explicit
      ``w:val`` not in ``0``/``false``/``off``) is dropped wholesale --
      hidden text is gone, not just unhidden.
    - ``word/settings.xml``'s ``<w:rsids>`` block (the document's revision-
      session-id index) is removed if present.
    - ``word/people.xml`` (cached reviewer identity/presence info some Word
      versions write) is deleted if present, with its relationship entries.
    - The same run/tracked-change/comment-marker/hidden-run scrub is also
      applied to ``word/header*.xml``, ``word/footer*.xml``,
      ``word/footnotes.xml``, and ``word/endnotes.xml`` if present -- they
      share ``word/document.xml``'s WordprocessingML vocabulary and can
      carry the same tracked-change/comment/hidden-text markup.

Known gaps (documented rather than silently mishandled):
    - Row/cell-level tracked insert or delete (``w:trPr``/``w:tcPr``
      containing an empty ``w:ins``/``w:del`` marker, used for a whole
      inserted/deleted table row or cell) only has its marker element
      stripped here; the row/cell itself is not structurally added or
      removed. The much more common case -- run-level ``w:ins``/``w:del``
      wrapping inline text -- is handled correctly.
    - A paragraph-mark deletion marker (``w:pPr/w:rPr/w:del``, empty) is
      stripped as a marker only; the two paragraphs are not actually merged
      the way Word's real "Accept All Changes" would. The paragraph mark
      itself is left alone (not deleted).
    - Scattered ``w:rsid*`` attributes on individual elements throughout the
      body (``w:rsidR``, ``w:rsidRPr``, ``w:rsidSect``, ...) are left as-is;
      only ``settings.xml``'s consolidated ``<w:rsids>`` index is scrubbed,
      per the plan's literal scope.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .archive_guard import stream_zip_member, validate_docx_archive

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

_DOCUMENT_PART = "word/document.xml"

#: WordprocessingML parts that can carry tracked changes / comments / hidden
#: runs, beyond the main body.
_WORDML_PART_RE = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")
_COMMENTS_PART_RE = re.compile(r"^word/comments[^/]*\.xml$")
_RELS_MEMBER_RE = re.compile(r"^(?:(.*)/)?_rels/([^/]+)\.rels$")

_DOCPROPS_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")
_PEOPLE_PART = "word/people.xml"

# resolve_entities=False + load_dtd=False: an XXE payload fails to parse
# rather than being resolved; no_network=True blocks any DTD/entity fetch
# too. Mirrors latextify.citations.endnote_xml_in's parser -- defined locally
# (rather than imported) so this module has no dependency on a shared
# XML-hardening helper that may land concurrently elsewhere.
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)


@dataclass
class CleanReport:
    """Summary of what :func:`sanitize_docx` removed, for CLI/GUI reporting."""

    tracked_changes_accepted: int = 0
    comments_removed: int = 0
    hidden_runs_removed: int = 0
    docprops_stripped: bool = False
    rsids_scrubbed: bool = False


def _q(localname: str) -> str:
    return f"{{{W}}}{localname}"


def sanitize_docx(src: Path | str, dest: Path | str) -> CleanReport:
    """Write a metadata/review-stripped copy of ``src`` to ``dest``.

    ``src`` is validated as a safe, well-formed .docx archive
    (:func:`~latextify.ingest.archive_guard.validate_docx_archive`) before
    anything is read from it. The result is a full copy of the archive with:
    docProps stripped, tracked changes accepted (insertions kept, deletions
    dropped), comments deleted, hidden (``w:vanish``) runs dropped, and
    ``settings.xml`` rsids scrubbed -- see the module docstring for the full
    list and known gaps. ``word/document.xml``'s overall structure, and every
    other archive member not implicated in the above, passes through
    unchanged.

    Raises:
        ValueError: ``src`` does not have a ``.docx`` extension, is not a
            valid/safe ZIP archive, or has no ``word/document.xml`` part.
    """
    src = Path(src)
    dest = Path(dest)
    if src.suffix.lower() != ".docx":
        raise ValueError(f"{src}: not a .docx file (expected a .docx extension)")

    validate_docx_archive(src)

    with zipfile.ZipFile(src) as zin:
        names = set(zin.namelist())
        if _DOCUMENT_PART not in names:
            raise ValueError(f"{src}: not a valid .docx (missing {_DOCUMENT_PART})")

        docprops_present = [name for name in _DOCPROPS_PARTS if name in names]
        comment_parts = sorted(name for name in names if _COMMENTS_PART_RE.match(name))
        people_present = [_PEOPLE_PART] if _PEOPLE_PART in names else []
        parts_to_remove = set(docprops_present) | set(comment_parts) | set(people_present)

        drop_members = set(parts_to_remove)
        for name in names:
            if name.endswith(".rels"):
                owning = _owning_part(name)
                if owning is not None and owning in parts_to_remove:
                    drop_members.add(name)

        comments_removed = 0
        if "word/comments.xml" in names:
            comments_removed = _count_comments(zin.read("word/comments.xml"))

        replacements: dict[str, bytes] = {}
        tracked_changes_accepted = 0
        hidden_runs_removed = 0
        for name in sorted(names):
            if name in drop_members or not _WORDML_PART_RE.match(name):
                continue
            new_bytes, tracked, hidden = _sanitize_wordml_part(zin.read(name))
            replacements[name] = new_bytes
            tracked_changes_accepted += tracked
            hidden_runs_removed += hidden

        rsids_scrubbed = False
        if "word/settings.xml" in names and "word/settings.xml" not in drop_members:
            new_settings, rsids_scrubbed = _scrub_settings(zin.read("word/settings.xml"))
            replacements["word/settings.xml"] = new_settings

        if "[Content_Types].xml" in names and parts_to_remove:
            replacements["[Content_Types].xml"] = _scrub_content_types(
                zin.read("[Content_Types].xml"), parts_to_remove
            )

        for name in names:
            if name in drop_members or not name.endswith(".rels"):
                continue
            base_dir = _rels_base_dir(name)
            new_rels, changed = _scrub_rels(zin.read(name), base_dir, parts_to_remove)
            if changed:
                replacements[name] = new_rels

        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_sanitized_archive(zin, dest, replacements, drop_members)

    return CleanReport(
        tracked_changes_accepted=tracked_changes_accepted,
        comments_removed=comments_removed,
        hidden_runs_removed=hidden_runs_removed,
        docprops_stripped=bool(docprops_present),
        rsids_scrubbed=rsids_scrubbed,
    )


def _write_sanitized_archive(
    zin: zipfile.ZipFile,
    dest: Path,
    replacements: dict[str, bytes],
    drop_members: set[str],
) -> None:
    """Copy ``zin`` to ``dest``, dropping ``drop_members`` and overwriting
    ``replacements`` -- the same bounded-streaming pattern
    :func:`~latextify.ingest.citation_sentinels.rewrite_archive_parts` uses,
    extended to support excluding members entirely (needed here since some
    parts, e.g. ``docProps/core.xml``, are deleted, not just rewritten)."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename in drop_members:
                continue
            replacement = replacements.get(item.filename)
            if replacement is not None:
                zout.writestr(item.filename, replacement)
            else:
                stream_zip_member(zin, zout, item)


# ── word/document.xml (+ headers/footers/footnotes/endnotes) ──────────────


def _sanitize_wordml_part(data: bytes) -> tuple[bytes, int, int]:
    """Return (new_bytes, tracked_changes_accepted, hidden_runs_removed)."""
    root = etree.fromstring(data, parser=_PARSER)

    tracked = 0
    tracked += _remove_all(root, "del")
    tracked += _unwrap_all(root, "ins")
    _remove_all(root, "delText")  # defensive: normally already gone with w:del
    tracked += _remove_change_elements(root)

    _remove_all(root, "commentRangeStart")
    _remove_all(root, "commentRangeEnd")
    _remove_all(root, "commentReference")

    hidden = _remove_hidden_runs(root)

    new_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return new_bytes, tracked, hidden


def _remove_all(root: etree._Element, localname: str) -> int:
    """Remove every ``w:<localname>`` element found anywhere under ``root``."""
    removed = 0
    for elem in root.findall(f".//{_q(localname)}"):
        parent = elem.getparent()
        if parent is not None:
            parent.remove(elem)
            removed += 1
    return removed


def _unwrap_all(root: etree._Element, localname: str) -> int:
    """Replace every ``w:<localname>`` element with its own children.

    Used for ``w:ins``: the wrapper is a tracked-insertion marker, not
    content -- "accepting" it means keeping the children (the inserted
    text/runs) and dropping only the marker. An empty wrapper (the
    paragraph-mark-insertion marker, ``w:pPr/w:rPr/w:ins``) degrades to a
    plain removal, which is the correct accept behaviour for that case too.
    """
    count = 0
    for elem in root.findall(f".//{_q(localname)}"):
        parent = elem.getparent()
        if parent is None:
            continue
        index = parent.index(elem)
        children = list(elem)
        for offset, child in enumerate(children):
            parent.insert(index + offset, child)
        if elem.tail:
            if children:
                children[-1].tail = (children[-1].tail or "") + elem.tail
            elif index > 0:
                prev = parent[index - 1]
                prev.tail = (prev.tail or "") + elem.tail
            else:
                parent.text = (parent.text or "") + elem.tail
        parent.remove(elem)
        count += 1
    return count


def _remove_change_elements(root: etree._Element) -> int:
    """Remove every ``w:*Change`` element (``rPrChange``, ``pPrChange``, ...)."""
    removed = 0
    for elem in list(root.iter()):
        if not isinstance(elem.tag, str):
            continue  # skip comments/PIs, which have no QName
        qname = etree.QName(elem)
        if qname.namespace == W and qname.localname.endswith("Change"):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
                removed += 1
    return removed


def _ooxml_bool(val: str | None) -> bool:
    """OOXML ``ST_OnOff``: absence of ``w:val`` means true; ``0``/``false``/``off`` mean false."""
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "off")


def _remove_hidden_runs(root: etree._Element) -> int:
    """Drop every run (``w:r``) whose ``w:rPr/w:vanish`` is active."""
    removed = 0
    for run in list(root.iter(_q("r"))):
        rpr = run.find(_q("rPr"))
        if rpr is None:
            continue
        vanish = rpr.find(_q("vanish"))
        if vanish is None:
            continue
        if _ooxml_bool(vanish.get(_q("val"))):
            parent = run.getparent()
            if parent is not None:
                parent.remove(run)
                removed += 1
    return removed


# ── word/settings.xml ───────────────────────────────────────────────────


def _scrub_settings(data: bytes) -> tuple[bytes, bool]:
    """Remove ``<w:rsids>`` (the revision-session-id index) if present."""
    root = etree.fromstring(data, parser=_PARSER)
    rsids = root.find(_q("rsids"))
    scrubbed = False
    if rsids is not None:
        parent = rsids.getparent()
        if parent is not None:
            parent.remove(rsids)
            scrubbed = True
    new_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return new_bytes, scrubbed


# ── word/comments.xml ───────────────────────────────────────────────────


def _count_comments(data: bytes) -> int:
    try:
        root = etree.fromstring(data, parser=_PARSER)
    except etree.XMLSyntaxError:
        return 0
    return len(root.findall(_q("comment")))


# ── [Content_Types].xml / *.rels package-consistency bookkeeping ─────────


def _owning_part(rels_member: str) -> str | None:
    """The part a ``.../_rels/NAME.rels`` file describes relationships FOR.

    Returns ``None`` for the package-root ``_rels/.rels`` (not tied to a
    single removable part) or any name that doesn't match the ``.rels``
    naming convention.
    """
    if rels_member == "_rels/.rels":
        return None
    match = _RELS_MEMBER_RE.match(rels_member)
    if not match:
        return None
    dir_part, name = match.group(1) or "", match.group(2)
    return f"{dir_part}/{name}" if dir_part else name


def _rels_base_dir(rels_member: str) -> str:
    """Directory a ``.rels`` file's relative ``Target`` values resolve against."""
    if rels_member == "_rels/.rels":
        return ""
    match = _RELS_MEMBER_RE.match(rels_member)
    return (match.group(1) or "") if match else ""


def _resolve_target(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    joined = posixpath.join(base_dir, target) if base_dir else target
    return posixpath.normpath(joined)


def _scrub_rels(data: bytes, base_dir: str, parts_to_remove: set[str]) -> tuple[bytes, bool]:
    """Drop any ``<Relationship>`` whose ``Target`` resolves into ``parts_to_remove``."""
    root = etree.fromstring(data, parser=_PARSER)
    changed = False
    for rel in list(root):
        if not isinstance(rel.tag, str):
            continue
        if rel.get("TargetMode") == "External":
            continue  # not a package part; nothing to resolve/drop
        target = rel.get("Target")
        if target is None:
            continue
        if _resolve_target(base_dir, target) in parts_to_remove:
            root.remove(rel)
            changed = True
    if not changed:
        return data, False
    new_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return new_bytes, True


def _scrub_content_types(data: bytes, parts_to_remove: set[str]) -> bytes:
    """Drop each removed part's ``<Override>`` entry from ``[Content_Types].xml``."""
    root = etree.fromstring(data, parser=_PARSER)
    wanted = {f"/{part}" for part in parts_to_remove}
    for override in root.findall(f"{{{_CT_NS}}}Override"):
        if override.get("PartName") in wanted:
            root.remove(override)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
