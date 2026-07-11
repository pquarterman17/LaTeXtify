"""Word-native bibliography citation extraction.

Word's own References > Insert Citation tool (no Zotero/Mendeley/EndNote
plugin involved) stores bibliography sources as ``b:Source`` elements --
``b`` = ``http://schemas.openxmlformats.org/officeDocument/2006/bibliography``
-- inside one or more ``customXml/item*.xml`` parts, one ``b:Source`` per
reference, keyed by its ``b:Tag`` (e.g. ``"Smi20"``).

An in-text citation is a ``w:sdt`` structured-document-tag content control
marked as a citation placeholder (an empty ``<w:citation/>`` child of
``w:sdtPr``). The reference(s) it points at are recorded as a genuine Word
field -- the SAME ``w:fldChar``/``w:instrText``/``w:fldSimple`` machinery
:mod:`latextify.citations.fields` already walks -- nested inside
``w:sdtContent``, with an instruction of the form ``CITATION <Tag1> \\l
<lcid>`` (additional sources in a multi-cite are appended as `` \\m
<TagN>``). Because :func:`~latextify.citations.fields.assemble_fields_from_root`
walks the whole tree with ``root.iter()``, it already recovers this field
wherever it is nested -- inside a ``w:sdt`` just like inside a ``PAGEREF``
(see the nested-field case exercised by ``zotero_cited.docx``) -- with zero
changes needed to the walker or the sentinel planter; only
:func:`~latextify.citations.fields.classify_marker` needs one additive
branch (:func:`matches`) to recognize the ``CITATION `` marker.

:func:`parse_tag_list` splits an assembled instruction into its ordered
list of ``b:Tag`` references. :func:`load_sources` reads every
``customXml/item*.xml`` part whose root is a ``b:Sources`` element into a
``tag -> RefEntry`` map, skipping any part that isn't one (customXml holds
many unrelated things) and any individual ``b:Source`` that fails to parse
(missing ``b:Tag``), so one malformed source never affects its siblings.
:func:`parse_instruction` combines the two: given an assembled ``CITATION``
field instruction and the tag map, it returns the ordered ``RefEntry`` list
for that in-text citation -- an unresolved tag is silently skipped, never
raised.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from ..model.refs import Name, RefEntry
from .bib import csl_type_to_bibtex

B = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"

MARKER = "CITATION"

_ITEM_PART_RE = re.compile(r"customXml/item\d+\.xml")

_SOURCE_TYPE_TO_CSL = {
    "JournalArticle": "article-journal",
    "ArticleInAPeriodical": "article-magazine",
    "Book": "book",
    "BookSection": "chapter",
    "ConferenceProceedings": "paper-conference",
    "Report": "report",
    "InternetSite": "webpage",
    "DocumentFromInternetSite": "webpage",
    "Patent": "patent",
    "Misc": "misc",
}

# Word CITATION field switches that take one following argument and are NOT
# themselves a b:Tag: \l <lcid>, \p <page>, \v <volume>, \f <bookmark tag>,
# \s <sort key>. Only \m introduces an ADDITIONAL tag reference.
_ARG_SWITCHES = {"\\l", "\\p", "\\v", "\\f", "\\s"}


def matches(instruction: str) -> bool:
    """True for a Word-native bibliography ``CITATION`` field instruction."""
    stripped = instruction.strip()
    return stripped == "CITATION" or stripped.upper().startswith("CITATION ")


def parse_tag_list(instruction: str) -> list[str]:
    """Extract the ordered list of ``b:Tag`` references from a CITATION field.

    Word encodes a multi-source citation as
    ``CITATION Tag1 \\l 1033 \\m Tag2 \\l 1033``: the first tag follows the
    ``CITATION`` keyword directly; each additional source is introduced by
    ``\\m <Tag>``. Other switches (``\\l``, ``\\p``, ...) take one argument
    that is not a tag and is skipped.
    """
    stripped = instruction.strip()
    if not stripped.upper().startswith("CITATION"):
        return []
    tokens = stripped[len("CITATION") :].split()
    tags: list[str] = []
    i = 0
    if tokens and not tokens[0].startswith("\\"):
        tags.append(tokens[0])
        i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "\\m" and i + 1 < len(tokens):
            tags.append(tokens[i + 1])
            i += 2
        elif token in _ARG_SWITCHES and i + 1 < len(tokens):
            i += 2
        else:
            i += 1
    return tags


def _qb(name: str) -> str:
    return f"{{{B}}}{name}"


def _leaf_text(elem, path: str) -> str | None:
    found = elem.find(_qb(path))
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _parse_person(person) -> Name:
    last = _leaf_text(person, "Last") or ""
    first = _leaf_text(person, "First") or ""
    middle = _leaf_text(person, "Middle")
    given = " ".join(part for part in (first, middle) if part)
    return Name(family=last, given=given)


def _parse_contributor_role(source, role: str) -> tuple[Name, ...]:
    """Names for one contributor role: ``b:Author/b:<role>/b:NameList/b:Person``."""
    author_elem = source.find(_qb("Author"))
    if author_elem is None:
        return ()
    role_elem = author_elem.find(_qb(role))
    if role_elem is None:
        return ()
    name_list = role_elem.find(_qb("NameList"))
    if name_list is not None:
        names = tuple(_parse_person(p) for p in name_list.findall(_qb("Person")))
        return tuple(n for n in names if n.family or n.given or n.literal)
    corporate = _leaf_text(role_elem, "Corporate")
    return (Name(literal=corporate),) if corporate else ()


def source_to_refentry(source_elem) -> RefEntry | None:
    """Convert one ``<b:Source>`` element to a keyless ``RefEntry``.

    Returns ``None`` (never raises) when the source has no ``b:Tag`` --
    without a tag it can never be matched to an in-text citation, so it is
    skipped rather than surfaced as a phantom, unreachable entry.
    """
    tag = _leaf_text(source_elem, "Tag")
    if not tag:
        return None
    source_type = _leaf_text(source_elem, "SourceType") or ""
    csl_type = _SOURCE_TYPE_TO_CSL.get(source_type, "")
    container = (
        _leaf_text(source_elem, "JournalName")
        or _leaf_text(source_elem, "ConferenceName")
        or _leaf_text(source_elem, "BookTitle")
    )
    return RefEntry(
        key="",
        entry_type=csl_type_to_bibtex(csl_type),
        csl_type=csl_type,
        title=_leaf_text(source_elem, "Title"),
        authors=_parse_contributor_role(source_elem, "Author"),
        year=_leaf_text(source_elem, "Year"),
        container_title=container,
        publisher=_leaf_text(source_elem, "Publisher"),
        volume=_leaf_text(source_elem, "Volume"),
        pages=_leaf_text(source_elem, "Pages"),
        doi=_leaf_text(source_elem, "DOI"),
        url=_leaf_text(source_elem, "URL"),
        source="wordnative",
        raw_id=tag,
    )


def load_sources(docx_path: Path | str) -> dict[str, RefEntry]:
    """Read every ``customXml/item*.xml`` Sources part into a tag -> RefEntry map.

    A part that fails to parse as XML, or whose root isn't ``b:Sources``, is
    silently skipped -- customXml holds many parts unrelated to the
    bibliography. A malformed individual ``b:Source`` (missing its Tag) is
    skipped without affecting its siblings.
    """
    sources: dict[str, RefEntry] = {}
    with zipfile.ZipFile(docx_path) as archive:
        names = [n for n in archive.namelist() if _ITEM_PART_RE.fullmatch(n)]
        for name in names:
            try:
                root = etree.fromstring(archive.read(name))
            except etree.XMLSyntaxError:
                continue
            if etree.QName(root.tag).localname != "Sources":
                continue
            for source_elem in root.findall(_qb("Source")):
                entry = source_to_refentry(source_elem)
                if entry is not None and entry.raw_id is not None:
                    sources[entry.raw_id] = entry
    return sources


def parse_instruction(instruction: str, sources: dict[str, RefEntry]) -> list[RefEntry]:
    """Resolve a CITATION field instruction's tags against the sources map.

    An unresolvable tag (dangling reference, or ``load_sources`` found no
    bibliography part at all) is skipped -- never raised -- so one bad
    reference doesn't take down the whole citation.
    """
    return [sources[tag] for tag in parse_tag_list(instruction) if tag in sources]
