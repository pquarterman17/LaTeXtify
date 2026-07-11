"""Plant alphanumeric citation sentinels into a .docx before pandoc conversion.

WHY THIS EXISTS (plan item 24): pandoc 3.9's docx reader does not turn
Zotero/Mendeley/EndNote citation field codes into native ``Cite`` AST nodes --
it emits only each field's cached display text ("[1]", "[2, 3]", ...) as plain
``Str`` runs. So the ``%%CITE:<idx>%%`` anchor path in
:func:`latextify.ingest.filters.plant_anchors` never fires for field-coded
citations and no ``\\cite{}`` reaches the body (bibliography extraction, done
straight from the XML by :mod:`latextify.citations.fields`, is unaffected).

The fix runs BEFORE pandoc: overwrite each citation field's displayed RESULT
with an alphanumeric-only sentinel ``ZZLTXCITE<i>ZZ``. The sentinel must be
alphanumeric because pandoc's LaTeX writer escapes ``%`` -> ``\\%``, so a
``%%CITE%%``-style marker would be mangled; alphanumeric text survives verbatim
(including when it lands adjacent to other text, e.g. ``ZZLTXCITE2ZZSection``).
pandoc carries the sentinel into the body, and the emitter
(:func:`latextify.emit.project.emit_project`) swaps it for ``\\cite{key,...}``.

Index ``i`` is 0-based in the SAME document-order field walk
:func:`latextify.citations.fields.extract_field_citations` uses (the walker is
shared, not reimplemented), so ``ZZLTXCITE<i>ZZ`` pairs with the ``Citation``
whose ``.index`` is ``i`` -- including a citation field nested inside another
field (e.g. a Zotero cite inside a ``PAGEREF``), which the shared walk
enumerates identically on both sides.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from ..citations.fields import (
    FieldResult,
    assemble_fields_from_root,
    classify_marker,
    flatten_fields,
    read_document_xml,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCUMENT_PART = "word/document.xml"

_SENTINEL_PREFIX = "ZZLTXCITE"
_SENTINEL_SUFFIX = "ZZ"
#: Matches a planted sentinel and captures its 0-based citation index.
SENTINEL_RE = re.compile(_SENTINEL_PREFIX + r"(\d+)" + _SENTINEL_SUFFIX)


def sentinel_for(index: int) -> str:
    """The body sentinel string for the citation at document-order ``index``."""
    return f"{_SENTINEL_PREFIX}{index}{_SENTINEL_SUFFIX}"


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def _make_sentinel_run(text: str):
    """A minimal ``w:r``/``w:t`` run carrying the sentinel text."""
    run = etree.Element(_q("r"))
    t = etree.SubElement(run, _q("t"))
    t.text = text
    return run


def _plant(result: FieldResult | None, sentinel: str) -> bool:
    """Replace a field's displayed result with a single sentinel run.

    Returns True on success. Degrades to a no-op (False) for a malformed field
    whose end/container references were never captured, rather than raising.
    """
    if result is None:
        return False
    run = _make_sentinel_run(sentinel)
    if result.kind == "simple":
        elem = result.simple_elem
        if elem is None:
            return False
        parent = elem.getparent()
        if parent is None:
            return False
        # pandoc 3.9 renders NONE of a w:fldSimple's inner content (verified for
        # ADDIN CSL_CITATION and even for a plain PAGE field), so the sentinel
        # goes as a SIBLING immediately after the field. The cached result runs
        # are still cleared so a future, less lossy pandoc can't double-render.
        for existing in result.result_runs:
            if existing.getparent() is elem:
                elem.remove(existing)
        parent.insert(parent.index(elem) + 1, run)
        return True
    # Complex field: drop any existing result runs and insert the sentinel
    # immediately before the 'end' run. This handles the result-present,
    # result-absent (no 'separate'), and empty-result cases uniformly.
    end_run = result.end_run
    if end_run is None:
        return False
    container = end_run.getparent()
    if container is None:
        return False
    for existing in result.result_runs:
        if existing.getparent() is container:
            container.remove(existing)
    container.insert(container.index(end_run), run)
    return True


def plant_citation_sentinels(docx_path: Path | str, work_dir: Path | str) -> Path:
    """Return a .docx whose citation-field results are sentinel-tagged.

    Walks ``docx_path``'s ``word/document.xml`` fields in document order (the
    shared :func:`~latextify.citations.fields.assemble_fields_from_root` walk)
    and, for the i-th recognized citation field, replaces its displayed result
    with :func:`sentinel_for` (i).

    If the document has no recognized citation fields, returns ``docx_path``
    unchanged and writes nothing (so non-citation documents pass through
    untouched). Otherwise writes a full copy of the archive into ``work_dir``
    with only ``word/document.xml`` rewritten and returns the copy's path.
    """
    docx_path = Path(docx_path)
    root = etree.fromstring(read_document_xml(docx_path))

    fields = flatten_fields(assemble_fields_from_root(root))
    citation_fields = [f for f in fields if classify_marker(f.instruction)]
    if not citation_fields:
        return docx_path

    for index, fld in enumerate(citation_fields):
        _plant(fld.result, sentinel_for(index))

    new_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / f"sentinel-{docx_path.name}"
    _rewrite_archive(docx_path, dest, {_DOCUMENT_PART: new_xml})
    return dest


def _rewrite_archive(src: Path, dest: Path, replacements: dict[str, bytes]) -> None:
    """Copy the ``src`` zip to ``dest``, overwriting the named archive parts."""
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            data = replacements.get(item.filename)
            zout.writestr(item.filename, zin.read(item.filename) if data is None else data)
