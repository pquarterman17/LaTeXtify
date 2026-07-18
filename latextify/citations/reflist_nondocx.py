"""Typed reference-list segmentation for non-.docx manuscripts.

Companion to :func:`latextify.citations.plaintext.segment_reference_list`:
.odt/.rtf/.md have no ``word/document.xml`` to read directly, so
:func:`segment_reference_list_from_manuscript` walks pandoc's own AST
instead, reusing the exact same heading/leading-number regexes as the docx
path so "confident reference list" means the same thing on both.

Kept in its own module rather than inlined into ``plaintext.py`` to stay
clear of that file's line-count ratchet pin (``tests/test_repo_integrity.py``)
-- the import back into ``plaintext.py``'s own ``ReferenceList`` /
``_is_heading_paragraph`` / ``_LIST_NUMBER_RE`` is done lazily, inside
:func:`segment_reference_list_from_manuscript`, the same way
:mod:`latextify.citations.reconcile` lazily imports :mod:`latextify.citations.bibmatch`
to break their own two-way dependency.
"""

from __future__ import annotations

import io
from pathlib import Path

import panflute as pf
import pypandoc

from ..ingest.formats import pandoc_format_for
from .reconcile import ReferenceItem


def _manuscript_paragraphs(manuscript_path: Path) -> list[tuple[str, bool]]:
    """``[(text, is_heading)]`` for a non-docx manuscript, via pandoc's own AST.

    Non-docx stand-in for reading paragraphs directly out of
    ``word/document.xml``: a ``Header`` block is a heading, a top-level
    ``Para``/``Plain`` block is an ordinary paragraph. ``pf.stringify`` returns
    plain text (no LaTeX escaping), matching what the docx path reads from raw
    ``w:t`` runs -- the leading-list-number regex expects an unescaped
    ``"[12]"``/``"12."``, not pandoc's LaTeX-writer-escaped ``"{[}12{]}"``. A
    reference list authored with markdown/RTF native list syntax (an
    ``OrderedList``/``BulletList`` AST node rather than bare paragraphs) is
    not walked here; a typed ``"[1] ..."``/``"1. ..."`` paragraph list -- the
    common case, and the only case the docx path's own auto-numbering fallback
    handles -- is fully supported.
    """
    fmt = pandoc_format_for(manuscript_path)
    ast_json = pypandoc.convert_file(str(manuscript_path), to="json", format=fmt)
    doc = pf.load(io.StringIO(ast_json))
    paragraphs: list[tuple[str, bool]] = []
    for block in doc.content:
        if isinstance(block, pf.Header):
            paragraphs.append((pf.stringify(block).strip(), True))
        elif isinstance(block, (pf.Para, pf.Plain)):
            paragraphs.append((pf.stringify(block).strip(), False))
    return paragraphs


def segment_reference_list_from_manuscript(manuscript_path: Path):
    """Non-docx equivalent of ``latextify.citations.plaintext.segment_reference_list``.

    Same heading + leading-list-number detection as the docx path, over
    :func:`_manuscript_paragraphs` instead of a ``word/document.xml`` walk.
    Word's own auto-numbered lists (``w:numPr``) have no equivalent here -- a
    numberless typed reference degrades to ``number=None``, same as an
    unrecognized number on the docx path.
    """
    from .plaintext import _LIST_NUMBER_RE, ReferenceList, _is_heading_paragraph

    paragraphs = _manuscript_paragraphs(manuscript_path)

    heading_index: int | None = None
    heading_text: str | None = None
    for index, (text, is_heading) in enumerate(paragraphs):
        if is_heading and _is_heading_paragraph(text):
            heading_index = index
            heading_text = text.strip().rstrip(":").strip()
            break

    if heading_index is None:
        return ReferenceList(heading=None)

    references: list[ReferenceItem] = []
    for text, _is_heading in paragraphs[heading_index + 1 :]:
        text = text.strip()
        if not text:
            continue
        match = _LIST_NUMBER_RE.match(text)
        if match:
            number = int(match.group("br") or match.group("pr") or match.group("dot"))
            body = text[match.end() :].strip()
            references.append(ReferenceItem(text=body, number=number))
        else:
            references.append(ReferenceItem(text=text, number=None))

    return ReferenceList(heading=heading_text, references=references)
