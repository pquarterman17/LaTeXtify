"""Word complex-field walker + field-code citation extraction.

Word stores a "complex field" as a run of pieces spread across several ``w:r``
runs: a ``w:fldChar w:fldCharType="begin"``, then one or more ``w:instrText``
runs whose text must be CONCATENATED to recover the instruction, an optional
``w:fldChar w:fldCharType="separate"``, the displayed result, and a
``w:fldChar w:fldCharType="end"``. Fields also NEST (a field may open inside
another field's region). ``w:fldSimple`` is the collapsed single-element form.

:func:`assemble_fields` reconstructs the field tree with instruction text
correctly attributed per field; :func:`extract_field_citations` classifies each
field by its ADDIN marker, dispatches Zotero/Mendeley payloads to their
parsers, de-duplicates references across the document, assigns stable BibTeX
keys, and returns document-ordered :class:`~latextify.model.refs.Citation`
records ready for ``%%CITE:<idx>%%`` anchor pairing by the emitter.
"""

from __future__ import annotations

import itertools
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from ..model.refs import Citation, RefEntry
from . import mendeley, zotero
from .bib import assign_keys

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def read_document_xml(docx_path) -> bytes:
    """Read ``word/document.xml`` bytes from a .docx (ZIP) file."""
    with zipfile.ZipFile(docx_path) as archive:
        return archive.read("word/document.xml")


@dataclass
class ComplexField:
    """One assembled Word field.

    ``instruction`` is the concatenated ``w:instrText`` for THIS field only
    (a nested field's text stays with the nested field). ``order`` is the
    field's begin position in the document (a pre-order index). ``children``
    are fields nested inside this one.
    """

    instruction: str
    order: int
    children: list[ComplexField] = field(default_factory=list)


class _Frame:
    __slots__ = ("field", "collecting")

    def __init__(self, fld: ComplexField) -> None:
        self.field = fld
        self.collecting = True  # instrText belongs to instruction until 'separate'


def assemble_fields(document_xml: bytes) -> list[ComplexField]:
    """Reconstruct the tree of complex/simple fields from document.xml bytes.

    Returns top-level fields (each with nested ``children``). A stack tracks
    open ``begin``/``end`` pairs so instruction text is attributed to the
    correct (possibly nested) field even when split across many runs.
    """
    root = etree.fromstring(document_xml)
    top: list[ComplexField] = []
    stack: list[_Frame] = []
    order = itertools.count()

    def _attach(fld: ComplexField) -> None:
        (stack[-1].field.children if stack else top).append(fld)

    for element in root.iter():
        tag = element.tag
        if not isinstance(tag, str):
            continue  # comments / processing instructions
        local = etree.QName(tag).localname
        if local == "fldChar":
            char_type = element.get(_q("fldCharType"))
            if char_type == "begin":
                stack.append(_Frame(ComplexField("", next(order))))
            elif char_type == "separate":
                if stack:
                    stack[-1].collecting = False
            elif char_type == "end":
                if stack:
                    frame = stack.pop()
                    frame.field.instruction = frame.field.instruction.strip()
                    _attach(frame.field)
        elif local == "instrText":
            if stack and stack[-1].collecting:
                stack[-1].field.instruction += element.text or ""
        elif local == "fldSimple":
            instr = (element.get(_q("instr")) or "").strip()
            _attach(ComplexField(instr, next(order)))

    # Attach any fields left open by a malformed document.
    while stack:
        frame = stack.pop()
        frame.field.instruction = frame.field.instruction.strip()
        _attach(frame.field)

    return top


def flatten_fields(fields: list[ComplexField]) -> list[ComplexField]:
    """Depth-first (pre-order) flatten, sorted by document begin order."""
    out: list[ComplexField] = []
    for fld in sorted(fields, key=lambda f: f.order):
        out.append(fld)
        out.extend(flatten_fields(fld.children))
    return out


def classify_marker(instruction: str) -> str | None:
    """Return the citation source for an instruction, or None if not a citation.

    Zotero is checked before Mendeley because both instructions contain the
    ``CSL_CITATION`` token; only Zotero carries ``ZOTERO_ITEM``.
    """
    if zotero.matches(instruction):
        return "zotero"
    if mendeley.matches(instruction):
        return "mendeley"
    return None


def _dedup_identity(entry: RefEntry) -> str:
    """A stable key identifying the same reference cited more than once."""
    if entry.doi:
        return "doi:" + entry.doi.strip().lower()
    if entry.raw_id:
        return "id:" + entry.raw_id
    families = "|".join(a.family or a.literal for a in entry.authors).lower()
    title = re.sub(r"\W+", "", (entry.title or "")).lower()
    return f"fp:{families}:{entry.year}:{title}"


@dataclass
class ExtractionResult:
    """Field-code citation extraction output.

    ``entries`` is the de-duplicated, key-assigned reference list (feeds
    ``bib.entries_to_bib``); ``citations`` is the document-ordered list whose
    ``index`` pairs with ``%%CITE:<index>%%`` body anchors.
    """

    entries: list[RefEntry] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)


def extract_field_citations(docx_path) -> ExtractionResult:
    """Extract Zotero/Mendeley field-code citations from a .docx.

    Walks fields in document order, parses each recognized citation field into
    RefEntry objects, de-duplicates references across the whole document,
    assigns collision-free BibTeX keys, and builds the ordered Citation list.
    """
    fields = flatten_fields(assemble_fields(read_document_xml(docx_path)))
    citation_fields = [(f, kind) for f in fields if (kind := classify_marker(f.instruction))]

    # Parse each citation field into its ordered RefEntry list.
    per_field: list[list[RefEntry]] = []
    for fld, kind in citation_fields:
        parser = zotero if kind == "zotero" else mendeley
        per_field.append(parser.parse_instruction(fld.instruction))

    # De-duplicate references, preserving first-seen order.
    unique: list[RefEntry] = []
    position: dict[str, int] = {}
    ids_per_field: list[list[str]] = []
    for entries in per_field:
        ids: list[str] = []
        for entry in entries:
            identity = _dedup_identity(entry)
            if identity not in position:
                position[identity] = len(unique)
                unique.append(entry)
            ids.append(identity)
        ids_per_field.append(ids)

    keyed = assign_keys(unique)
    identity_to_key = {identity: keyed[pos].key for identity, pos in position.items()}

    citations: list[Citation] = []
    for index, (_, kind) in enumerate(citation_fields):
        ordered_keys: list[str] = []
        seen: set[str] = set()
        for identity in ids_per_field[index]:
            key = identity_to_key[identity]
            if key not in seen:
                seen.add(key)
                ordered_keys.append(key)
        citations.append(Citation(index=index, keys=tuple(ordered_keys), source=kind))

    return ExtractionResult(entries=keyed, citations=citations)
