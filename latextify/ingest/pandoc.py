"""Pandoc invocation: manuscript -> pandoc JSON AST -> panflute filters -> LaTeX body.

:func:`convert_docx_to_body` is the single entry point callers use. It shells
out to the pandoc binary pypandoc-binary bundles twice (json-in, then
json->latex) with the panflute filters from :mod:`latextify.ingest.filters`
applied to the parsed tree in between. Doing it this way -- rather than
writing a pandoc JSON filter subprocess -- lets the filters be plain,
directly-testable Python functions over a ``panflute.Doc``.

Format routing (GUI_OPTIONS_FORMATS_PLAN item 9 -- distinct from "item 9" in
the paragraph below, which is this project's older figure-numbering item):
pandoc reads ``.docx``/``.odt``/``.rtf``/``.md`` natively, so the reader
format is picked from the extension via
:func:`~latextify.ingest.formats.pandoc_format_for`. Only ``.docx`` gets the
two OOXML-specific preprocessing passes below -- neither has an equivalent for
the other three formats, so a non-docx manuscript converts straight from the
source file:

Before pandoc runs on a .docx, :func:`~latextify.ingest.citation_sentinels.plant_citation_sentinels`
rewrites a temp copy so each Zotero/Mendeley citation field's displayed result
becomes an alphanumeric ``ZZLTXCITE<i>ZZ`` sentinel. This is necessary because
pandoc 3.9's docx reader does NOT turn those field codes into native ``Cite``
AST nodes (it emits only the cached display text), so the ``%%CITE`` anchor
path in :mod:`latextify.ingest.filters` never fires for them. The sentinels
reach the emitted body and the emitter (:mod:`latextify.emit.project`)
resolves them to ``\\cite{...}``; a document with no citation fields passes
through unchanged. A non-docx manuscript has no field codes to begin with (see
:mod:`latextify.citations.fields`), so this never applies to one.

``strip_front_matter`` (docx only) likewise has no equivalent for the other
formats: a non-docx main document's own typed title page is left in the body,
which can duplicate the journal metadata template's rendering -- noted as a
report warning by the emitter.

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
from latextify.ingest.formats import is_docx, pandoc_format_for
from latextify.ingest.frontmatter import strip_front_matter_from_docx
from latextify.model import BodyConversionResult


def convert_docx_to_body(
    docx_path: Path | str,
    media_dir: Path | str,
    *,
    strip_front_matter: bool = False,
) -> BodyConversionResult:
    """Convert a .docx manuscript body to a LaTeX fragment.

    Args:
        docx_path: path to the source .docx manuscript.
        media_dir: directory embedded images are extracted into (created if
            missing).
        strip_front_matter: when True (the emitter sets this for the main
            document), remove the manuscript's own title page from the body
            before conversion so it does not duplicate the journal metadata
            template's rendering (gap 4). Off by default so a document with no
            metadata context -- a supplement, or a direct body-fragment call --
            converts verbatim. See :mod:`latextify.ingest.frontmatter`.

    Returns:
        A :class:`~latextify.model.BodyConversionResult` with the emitted
        LaTeX text (anchors unresolved), the media directory, anchor
        counts, and any normalization findings.
    """
    docx_path = Path(docx_path)
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    pandoc_format = pandoc_format_for(docx_path)

    try:
        if is_docx(docx_path):
            # Plant citation sentinels into a throwaway copy first (see module
            # docstring); pandoc only reads the file, so the temp dir can go
            # away as soon as the AST is captured.
            with tempfile.TemporaryDirectory(prefix="latextify-cite-") as cite_tmp:
                prepared_docx = plant_citation_sentinels(docx_path, cite_tmp)
                if strip_front_matter:
                    # Sentinels rewrite runs inside citation fields but never add or
                    # remove paragraphs, so the title-page paragraph indices are
                    # identical in the sentinel copy -- detecting and stripping on
                    # it removes exactly the recognized front matter.
                    prepared_docx = strip_front_matter_from_docx(prepared_docx, cite_tmp)
                ast_json = pypandoc.convert_file(
                    str(prepared_docx),
                    to="json",
                    format=pandoc_format,
                    extra_args=["--extract-media", str(media_dir)],
                )
        else:
            # .odt/.rtf/.md: neither OOXML preprocessing pass above has an
            # equivalent here, so convert straight from the source file --
            # see the module docstring. --resource-path lets markdown's
            # externally-referenced images (![alt](fig.png), relative to the
            # manuscript, not pandoc's own cwd) resolve and get extracted.
            ast_json = pypandoc.convert_file(
                str(docx_path),
                to="json",
                format=pandoc_format,
                extra_args=[
                    "--extract-media", str(media_dir),
                    "--resource-path", str(docx_path.parent),
                ],
            )
        doc = pf.load(io.StringIO(ast_json))

        result = apply_all(doc)

        filtered_json = io.StringIO()
        pf.dump(result.doc, filtered_json)
        tex = pypandoc.convert_text(filtered_json.getvalue(), to="latex", format="json")
    except (RuntimeError, OSError) as exc:
        # preflight only validates word/document.xml and word/styles.xml
        # directly with lxml (see latextify.ingest.preflight); a .docx can
        # pass that check yet still have a structurally broken OOXML package
        # (missing [Content_Types].xml, a corrupt relationship, ...) that
        # only pandoc's own docx reader notices -- the same is true of the
        # other three formats' own readers. Never let that raw
        # pypandoc/subprocess failure reach the caller -- wrap it at this
        # ingest boundary the same way a bad zip or malformed XML is wrapped.
        raise ValueError(
            f"{docx_path}: pandoc failed to convert this document (it may be "
            f"corrupt or use a {pandoc_format} structure pandoc's reader "
            f"can't parse): {exc}"
        ) from exc

    return BodyConversionResult(
        tex=tex,
        media_dir=media_dir,
        figure_count=result.anchors.figures,
        citation_count=result.anchors.citations,
        findings=tuple(result.findings),
    )
