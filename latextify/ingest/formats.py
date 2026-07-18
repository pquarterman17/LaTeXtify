"""Manuscript-format detection shared by every docx-specific ingest stage
(GUI_OPTIONS_FORMATS_PLAN item 9: accept .odt/.rtf/.md manuscripts).

Pandoc reads ``.docx``/``.odt``/``.rtf``/``.md`` natively, so routing the body
conversion itself (:mod:`latextify.ingest.pandoc`) is nearly free -- just pick
the right ``--from`` format string. But several stages inspect the raw .docx
ZIP/XML structure DIRECTLY and have no equivalent for the other three formats:
preflight's structural inventory, metadata guessing, field-code citation
extraction, and the typed-reference-list segmentation inside the plain-text
citation fallback. Each of those stages needs to answer exactly one question
-- "is this a .docx?" -- the same way, so it lives here once rather than as a
``suffix == "docx"`` check copy-pasted at every call site.
"""

from __future__ import annotations

from pathlib import Path

#: Extension (lowercased, no dot) -> pandoc reader ``--from`` format name.
PANDOC_FORMAT_BY_EXT: dict[str, str] = {
    "docx": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "md": "markdown",
}


def manuscript_ext(path: Path | str) -> str:
    """Lowercased extension without the dot, e.g. ``"docx"``, ``"odt"``, ``"md"``."""
    return Path(path).suffix.lstrip(".").lower()


def pandoc_format_for(path: Path | str) -> str:
    """The pandoc ``--from`` format name for ``path``'s extension.

    Raises:
        ValueError: the extension isn't one of the recognized manuscript
            formats (same clean-error convention as every other ingest
            boundary in this package -- never a raw pandoc failure).
    """
    ext = manuscript_ext(path)
    try:
        return PANDOC_FORMAT_BY_EXT[ext]
    except KeyError:
        allowed = ", ".join("." + e for e in sorted(PANDOC_FORMAT_BY_EXT))
        raise ValueError(
            f"{path}: unrecognized manuscript file type '.{ext or '?'}' "
            f"(expected one of: {allowed})"
        ) from None


def is_docx(path: Path | str) -> bool:
    """True when ``path``'s extension is ``.docx``.

    Gates every docx-only ingest stage that has no equivalent for
    ``.odt``/``.rtf``/``.md``: preflight's OOXML inventory, docx-XML metadata
    guessing, field-code citation extraction (Zotero/Mendeley/EndNote/Word-
    native fields only exist in Word's own field-code machinery), and the
    typed-reference-list segmentation that reads paragraph/list-numbering
    structure straight from ``word/document.xml``.
    """
    return manuscript_ext(path) == "docx"


def is_alt_manuscript_format(path: Path | str) -> bool:
    """True when ``path``'s extension is one of the RECOGNIZED non-.docx
    manuscript formats -- ``.odt``/``.rtf``/``.md``, not just "anything that
    isn't .docx".

    Deliberately narrower than ``not is_docx(path)``: a file with a bogus or
    unrelated extension (``.zip``, ``.txt``, a corrupt/renamed file) is not
    one of the four recognized formats either, and should still fall through
    to the normal docx-shaped validation (which correctly rejects it) rather
    than silently degrading to "no findings" as if it were a legitimately
    supported alternate format.
    """
    return manuscript_ext(path) in ("odt", "rtf", "md")


def non_docx_warnings(path: Path | str, sidecar_existed: bool) -> list[str]:
    """Report-facing notes for a non-.docx manuscript, or ``[]`` for a .docx.

    Called once by the emitter (:mod:`latextify.emit.project`) so every
    degraded docx-only stage is acknowledged in report.md instead of being
    silently absent. ``sidecar_existed`` should be whether ``paper.yaml``
    existed BEFORE this run's metadata load -- the weak-guess note only
    applies to the run that actually guessed, never a later run reusing an
    already-written (possibly hand-edited) sidecar.
    """
    if is_docx(path):
        return []
    notes = [
        "manuscript is not .docx: preflight's structural checks and Word "
        "text-box figure captions do not apply to this format; only pandoc's "
        "own conversion runs. Use figures/fig<N>.<ext> beside the manuscript "
        "to add or override any figure this format doesn't capture."
    ]
    if not sidecar_existed:
        notes.append(
            "no paper.yaml sidecar found and this manuscript format has no "
            "author/affiliation extraction; a minimal placeholder title/author "
            "was guessed and written to paper.yaml -- edit it with the real "
            "title page before submitting."
        )
    return notes
