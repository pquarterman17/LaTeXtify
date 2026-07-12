"""Strip a manuscript's own title page from the .docx body before pandoc.

WHY THIS EXISTS (gap 4, found in the first real-manuscript shakedown): the
journal metadata template re-renders title / authors / affiliations / abstract
/ keywords from ``paper.yaml``. Pandoc, meanwhile, converts the manuscript body
verbatim -- so the manuscript's own typed title page lands in the body too, and
the compiled PDF shows all of it TWICE (once as the proper journal title block,
once as raw body text).

The fix runs BEFORE pandoc, mirroring the citation-sentinel preprocessing: the
recognized title-page paragraphs (the span from
:func:`latextify.ingest.metadata_guess.front_matter_span`, which uses the SAME
detection that builds ``paper.yaml``) are removed from a temp copy of
``word/document.xml``. Because the removed span equals what the metadata
template re-renders, nothing is duplicated and nothing is lost. A document with
no strong title-page signal (see ``front_matter_span``'s conservative gate) is
passed through unchanged -- this never guesses at removing body content.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..citations.fields import read_document_xml
from .citation_sentinels import rewrite_archive_parts
from .metadata_guess import front_matter_span

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCUMENT_PART = "word/document.xml"


def strip_front_matter_from_docx(docx_path: Path | str, work_dir: Path | str) -> Path:
    """Return a .docx whose recognized title-page paragraphs are removed.

    Removes the top-level ``w:p`` children of ``w:body`` in the span returned
    by :func:`~latextify.ingest.metadata_guess.front_matter_span` (enumerated
    identically -- direct ``w:p`` children in document order). If nothing is
    recognized (no strong title-page signal, or no ``w:body``), returns
    ``docx_path`` unchanged and writes nothing. Otherwise writes a full copy of
    the archive into ``work_dir`` with only ``word/document.xml`` rewritten and
    returns the copy's path.
    """
    docx_path = Path(docx_path)
    span = front_matter_span(docx_path)
    if span is None:
        return docx_path
    start, end = span

    root = etree.fromstring(read_document_xml(docx_path))
    body = root.find(f"{{{W}}}body")
    if body is None:
        return docx_path
    paragraphs = body.findall(f"{{{W}}}p")
    removed = 0
    for para in paragraphs[start:end]:
        parent = para.getparent()
        if parent is not None:
            parent.remove(para)
            removed += 1
    if removed == 0:
        return docx_path

    new_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / f"nofrontmatter-{docx_path.name}"
    rewrite_archive_parts(docx_path, dest, {_DOCUMENT_PART: new_xml})
    return dest
