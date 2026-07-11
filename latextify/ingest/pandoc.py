"""Pandoc invocation: docx -> pandoc JSON AST -> panflute filters -> LaTeX body.

:func:`convert_docx_to_body` is the single entry point callers use. It shells
out to the pandoc binary pypandoc-binary bundles twice (docx->json, then
json->latex) with the panflute filters from :mod:`latextify.ingest.filters`
applied to the parsed tree in between. Doing it this way -- rather than
writing a pandoc JSON filter subprocess -- lets the filters be plain,
directly-testable Python functions over a ``panflute.Doc``.

Before pandoc runs, :func:`~latextify.ingest.citation_sentinels.plant_citation_sentinels`
rewrites a temp copy of the docx so each Zotero/Mendeley citation field's
displayed result becomes an alphanumeric ``ZZLTXCITE<i>ZZ`` sentinel. This is
necessary because pandoc 3.9's docx reader does NOT turn those field codes into
native ``Cite`` AST nodes (it emits only the cached display text), so the
``%%CITE`` anchor path in :mod:`latextify.ingest.filters` never fires for them.
The sentinels reach the emitted body and the emitter
(:mod:`latextify.emit.project`) resolves them to ``\\cite{...}``; a document
with no citation fields passes through unchanged.

Embedded media is extracted via pandoc's ``--extract-media`` into
``media_dir`` as ``media/imageN.<ext>``, in document order; the
figures stage (item 9) associates those files with figure numbers and
captions.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import panflute as pf
import pypandoc

from latextify.ingest.citation_sentinels import plant_citation_sentinels
from latextify.ingest.filters import apply_all
from latextify.model import BodyConversionResult


def convert_docx_to_body(docx_path: Path | str, media_dir: Path | str) -> BodyConversionResult:
    """Convert a .docx manuscript body to a LaTeX fragment.

    Args:
        docx_path: path to the source .docx manuscript.
        media_dir: directory embedded images are extracted into (created if
            missing).

    Returns:
        A :class:`~latextify.model.BodyConversionResult` with the emitted
        LaTeX text (anchors unresolved), the media directory, anchor
        counts, and any normalization findings.
    """
    docx_path = Path(docx_path)
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    # Plant citation sentinels into a throwaway copy first (see module
    # docstring); pandoc only reads the file, so the temp dir can go away as
    # soon as the AST is captured.
    with tempfile.TemporaryDirectory(prefix="latextify-cite-") as cite_tmp:
        prepared_docx = plant_citation_sentinels(docx_path, cite_tmp)
        ast_json = pypandoc.convert_file(
            str(prepared_docx),
            to="json",
            format="docx",
            extra_args=["--extract-media", str(media_dir)],
        )
    doc = pf.load(io.StringIO(ast_json))

    result = apply_all(doc)

    filtered_json = io.StringIO()
    pf.dump(result.doc, filtered_json)
    tex = pypandoc.convert_text(filtered_json.getvalue(), to="latex", format="json")

    return BodyConversionResult(
        tex=tex,
        media_dir=media_dir,
        figure_count=result.anchors.figures,
        citation_count=result.anchors.citations,
        findings=tuple(result.findings),
    )
