"""Weak metadata guessing for non-.docx manuscripts (.odt/.rtf/.md).

Split out of :mod:`latextify.ingest.metadata_guess` (which sits at its own
line-count ratchet pin, ``tests/test_repo_integrity.py``): none of that
module's paragraph/style/superscript heuristics have an equivalent here (no
``word/document.xml`` to read), so this is a deliberately weak fallback, not
a second heuristic engine -- see :func:`guess_meta_minimal`.

The import back into ``metadata_guess.py``'s own ``MetaGuess`` is done
lazily, inside :func:`guess_meta_minimal`, the same way
:mod:`latextify.citations.reconcile` lazily imports :mod:`latextify.citations.bibmatch`
to break their own two-way dependency.
"""

from __future__ import annotations

import io
from pathlib import Path

import panflute as pf
import pypandoc

from ..model.meta import Author, Meta
from .formats import pandoc_format_for


def _first_heading_text(manuscript_path: Path) -> str:
    """First-level heading text via pandoc's own AST, or "" on any failure.

    Best-effort only: any pandoc failure here (unreadable file, no heading at
    all) must not block :func:`guess_meta_minimal`'s filename-stem fallback.
    """
    try:
        fmt = pandoc_format_for(manuscript_path)
        ast_json = pypandoc.convert_file(str(manuscript_path), to="json", format=fmt)
        doc = pf.load(io.StringIO(ast_json))
    except Exception:
        return ""
    for block in doc.content:
        if isinstance(block, pf.Header):
            text = pf.stringify(block).strip()
            if text:
                return text
    return ""


def guess_meta_minimal(manuscript_path: Path | str):
    """Best-effort ``Meta`` for a non-.docx manuscript (.odt/.rtf/.md).

    None of ``guess_meta``'s paragraph/style/superscript heuristics have an
    equivalent here (no ``word/document.xml`` to read), so this builds the
    weakest usable starting point: pandoc's own first-level heading as the
    title (falling back to the filename stem), and a single placeholder
    author -- a written sidecar must round-trip through
    ``meta_from_yaml_data``'s non-empty-authors rule on the next run, so an
    empty author list here would make THAT run crash instead of this one.
    Every field is flagged in ``checks`` (unlike a well-supported
    ``guess_meta`` guess, this one is never more than a placeholder); the
    caller (``load_or_create_meta``) surfaces a report-level warning on top
    of the ``# CHECK:`` comments these checks render as.
    """
    from .metadata_guess import MetaGuess  # lazy: breaks the two-way import (see module docstring)

    manuscript_path = Path(manuscript_path)
    title = _first_heading_text(manuscript_path) or manuscript_path.stem
    meta = Meta(title=title, authors=(Author(name="Unknown Author"),))
    checks = {
        "title": [
            "guessed from the manuscript's first heading (or its filename, if no "
            "heading was found) -- verify."
        ],
        "authors": [
            "this file format has no author/affiliation extraction; a placeholder "
            "author was written -- replace it with the real author list."
        ],
    }
    return MetaGuess(meta=meta, checks=checks)


def guess_meta_dispatch(manuscript_path: Path | str):
    """``guess_meta(path)`` for a ``.docx``, :func:`guess_meta_minimal` otherwise.

    Single dispatch point so ``latextify.ingest.metadata_guess.load_or_create_meta``
    needs only one import and one call here, keeping that module's own
    line-count ratchet pin (``tests/test_repo_integrity.py``) essentially
    untouched by this format-dispatch logic.
    """
    from .formats import is_docx
    from .metadata_guess import guess_meta

    if is_docx(manuscript_path):
        return guess_meta(manuscript_path)
    return guess_meta_minimal(manuscript_path)
