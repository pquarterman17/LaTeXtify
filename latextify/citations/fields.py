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

from ..ingest.formats import is_docx
from ..model.refs import Citation, RefEntry
from . import endnote, mendeley, wordnative, zotero
from .bib import assign_keys

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def read_document_xml(docx_path) -> bytes:
    """Read ``word/document.xml`` bytes from a .docx (ZIP) file."""
    with zipfile.ZipFile(docx_path) as archive:
        return archive.read("word/document.xml")


@dataclass
class FieldResult:
    """Positional handle to a field's displayed RESULT run(s) in the live tree.

    Populated by the same walk that builds a field's instruction (so it never
    drifts from the citation ordering) when that walk runs over a parsed tree
    via :func:`assemble_fields_from_root`. Its element references point into
    that tree, so a caller (the ingest citation-sentinel preprocessor) can
    overwrite the field's cached display text in place and re-serialize the
    tree. Fields that only need instructions/ordering ignore this.

    ``kind`` is ``"complex"`` (fldChar machinery) or ``"simple"`` (fldSimple).
    For a complex field ``end_run`` is the ``w:r`` wrapping the ``end`` fldChar
    (a replacement run is inserted immediately before it, which works whether
    or not a ``separate`` was present -- i.e. even when the result is absent).
    For a simple field ``simple_elem`` is the ``w:fldSimple`` element itself
    (the preprocessor clears its result runs and inserts the replacement as a
    following sibling, since pandoc does not render fldSimple inner content).
    ``result_runs`` are the existing displayed-result ``w:r`` elements (empty
    when the field has no result).
    """

    kind: str
    end_run: object | None = None
    simple_elem: object | None = None
    result_runs: list = field(default_factory=list)


@dataclass
class ComplexField:
    """One assembled Word field.

    ``instruction`` is the concatenated ``w:instrText`` for THIS field only
    (a nested field's text stays with the nested field). ``order`` is the
    field's begin position in the document (a pre-order index). ``children``
    are fields nested inside this one. ``result`` is populated only by
    :func:`assemble_fields_from_root` (``None`` from :func:`assemble_fields`),
    carrying the live-tree element references needed to rewrite the field's
    displayed text.
    """

    instruction: str
    order: int
    children: list[ComplexField] = field(default_factory=list)
    result: FieldResult | None = None


class _Frame:
    __slots__ = ("field", "collecting", "in_result")

    def __init__(self, fld: ComplexField) -> None:
        self.field = fld
        self.collecting = True  # instrText belongs to instruction until 'separate'
        self.in_result = False  # runs seen after 'separate' are the displayed result


def _local(element) -> str:
    """The namespace-stripped local name of an element, or "" for non-elements."""
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _is_field_machinery(run) -> bool:
    """True when a ``w:r`` wraps a fldChar/instrText rather than displayed text."""
    return any(_local(child) in ("fldChar", "instrText") for child in run)


def assemble_fields(document_xml: bytes) -> list[ComplexField]:
    """Reconstruct the tree of complex/simple fields from document.xml bytes.

    Returns top-level fields (each with nested ``children``). A stack tracks
    open ``begin``/``end`` pairs so instruction text is attributed to the
    correct (possibly nested) field even when split across many runs.
    """
    return assemble_fields_from_root(etree.fromstring(document_xml))


def assemble_fields_from_root(root) -> list[ComplexField]:
    """Same walk as :func:`assemble_fields`, over an already-parsed tree.

    Identical field-ordering/nesting semantics -- so it can be shared with the
    citation-sentinel preprocessor without a second, drift-prone walk -- but
    each returned :class:`ComplexField` additionally carries a populated
    :class:`FieldResult` whose element references point into ``root``. A caller
    that mutates those runs and re-serializes ``root`` rewrites the field's
    displayed text in place. ``root`` is an ``lxml`` element (e.g. from
    ``etree.fromstring(document_xml)``); pass the same document.xml bytes here
    that :func:`extract_field_citations` reads and the enumeration matches.
    """
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
                stack.append(_Frame(ComplexField("", next(order), result=FieldResult("complex"))))
            elif char_type == "separate":
                if stack:
                    stack[-1].collecting = False
                    stack[-1].in_result = True
            elif char_type == "end":
                if stack:
                    frame = stack.pop()
                    frame.field.instruction = frame.field.instruction.strip()
                    if frame.field.result is not None:
                        frame.field.result.end_run = element.getparent()
                    _attach(frame.field)
        elif local == "instrText":
            if stack and stack[-1].collecting:
                stack[-1].field.instruction += element.text or ""
        elif local == "fldSimple":
            instr = (element.get(_q("instr")) or "").strip()
            result = FieldResult(
                "simple",
                simple_elem=element,
                result_runs=[child for child in element if _local(child) == "r"],
            )
            _attach(ComplexField(instr, next(order), result=result))
        elif local == "r":
            # A displayed-text run: attribute it to the innermost open field
            # that is past its 'separate' (its result region). Skip field
            # machinery and runs already captured as a fldSimple's children.
            if _is_field_machinery(element):
                continue
            parent = element.getparent()
            if parent is not None and _local(parent) == "fldSimple":
                continue
            for frame in reversed(stack):
                if frame.in_result and frame.field.result is not None:
                    frame.field.result.result_runs.append(element)
                    break

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
    ``CSL_CITATION`` token; only Zotero carries ``ZOTERO_ITEM``. EndNote
    (``ADDIN EN.CITE``) and Word-native (``CITATION <Tag> ...``) markers are
    unambiguous and added additively (plan item 13).
    """
    if zotero.matches(instruction):
        return "zotero"
    if mendeley.matches(instruction):
        return "mendeley"
    if endnote.matches(instruction):
        return "endnote"
    if wordnative.matches(instruction):
        return "wordnative"
    return None


#: Fallback counter for entries with no usable dedup signal at all (DOI,
#: raw_id, authors, title, AND year all missing/empty) -- see
#: :func:`dedup_identity`.
_unidentified_counter = itertools.count()


def dedup_identity(entry: RefEntry) -> str:
    """A stable key identifying the same reference cited more than once.

    Public (plan item 21): :func:`latextify.citations.merge.merge_ref_entries`
    reuses this exact identity rule to dedupe a supplementary document's
    references against the main document's, rather than reimplementing the
    DOI -> raw_id -> author/year/title fingerprint precedence here.
    """
    if entry.doi:
        return "doi:" + entry.doi.strip().lower()
    if entry.raw_id:
        return "id:" + entry.raw_id
    families = "|".join(a.family or a.literal for a in entry.authors).lower()
    title = re.sub(r"\W+", "", (entry.title or "")).lower()
    if not families and not title and not entry.year:
        # No identifying data whatsoever (e.g. a Zotero/EndNote/wordnative
        # field whose itemData/record is missing author, title, year, DOI,
        # AND its own id -- catastrophically malformed but not impossible).
        # Two independently-cited references in this state would otherwise
        # share the same empty fingerprint "fp:::None:" and silently
        # collapse into one shared, wrong entry. Never merge them.
        return f"fp:unidentified:{next(_unidentified_counter)}"
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

    A non-.docx manuscript (.odt/.rtf/.md) has no Word field-code machinery to
    walk at all -- degrades to an empty result rather than raising, so the
    emitter's existing plain-text citation fallback
    (:mod:`latextify.citations.plaintext`) takes over unconditionally.
    """
    if not is_docx(docx_path):
        return ExtractionResult()
    fields = flatten_fields(assemble_fields(read_document_xml(docx_path)))
    citation_fields = [(f, kind) for f in fields if (kind := classify_marker(f.instruction))]

    # Word-native citations resolve against a document-wide tag -> RefEntry
    # map (customXml/item*.xml), unlike the other three sources whose payload
    # is self-contained in the field instruction -- load it lazily, once, and
    # only when a wordnative field is actually present.
    wordnative_sources: dict[str, RefEntry] | None = None
    if any(kind == "wordnative" for _, kind in citation_fields):
        wordnative_sources = wordnative.load_sources(docx_path)

    # Parse each citation field into its ordered RefEntry list.
    per_field: list[list[RefEntry]] = []
    for fld, kind in citation_fields:
        if kind == "zotero":
            per_field.append(zotero.parse_instruction(fld.instruction))
        elif kind == "mendeley":
            per_field.append(mendeley.parse_instruction(fld.instruction))
        elif kind == "endnote":
            per_field.append(endnote.parse_instruction(fld.instruction))
        else:  # "wordnative"
            per_field.append(
                wordnative.parse_instruction(fld.instruction, wordnative_sources or {})
            )

    # De-duplicate references, preserving first-seen order.
    unique: list[RefEntry] = []
    position: dict[str, int] = {}
    ids_per_field: list[list[str]] = []
    for entries in per_field:
        ids: list[str] = []
        for entry in entries:
            identity = dedup_identity(entry)
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
