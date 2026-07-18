"""Extension-based dispatch for every bibliography input format (plan item 10).

This is the SINGLE registration point on the pipeline side: whatever the
author hands in as ``references_bib_path`` (CLI ``--references``, or the
GUI's ``references`` upload -- see :mod:`latextify.emit.project`'s
``emit_project``), that file is read here, once, and routed to the right
parser by its extension. A new bibliography format needs exactly one new
entry in :data:`_TEXT_PARSERS` / :data:`_BYTES_PARSERS`; nothing that calls
:func:`parse_references_file` needs to change.

(The GUI's own upload allowlist, ``latextify.gui.server._ALLOWED_REFERENCE_EXTS``,
is a separate list -- it decides what's ACCEPTED for upload before anything
touches disk; this module decides how an already-accepted file is READ.)

``.ris`` is routed to the same BibTeX parser as ``.bib`` -- there is no
dedicated RIS grammar (yet); a real RIS file simply matches none of
``parse_bibtex``'s ``@type{...}`` patterns and degrades to zero offline
matches, same as today.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..model.refs import RefEntry
from .bibtex_in import parse_bibtex
from .csl_json_in import parse_csl_json
from .endnote_xml_in import parse_endnote_xml
from .nbib_in import parse_nbib

_TEXT_PARSERS: dict[str, Callable[[str], list[RefEntry]]] = {
    "bib": parse_bibtex,
    "ris": parse_bibtex,
    "json": parse_csl_json,
    "nbib": parse_nbib,
}
_BYTES_PARSERS: dict[str, Callable[[bytes], list[RefEntry]]] = {
    "xml": parse_endnote_xml,
}


def parse_references_file(path: Path | str) -> list[RefEntry]:
    """Parse a reference-manager export into :class:`RefEntry` records, by extension.

    Dispatches on ``path``'s suffix (case-insensitive): ``.bib``/``.ris`` ->
    BibTeX, ``.json`` -> CSL-JSON, ``.xml`` -> EndNote XML, ``.nbib`` ->
    PubMed MEDLINE.

    Raises:
        ValueError: the extension isn't one of the above, or the matched
            parser rejects the file's contents (each parser's own docstring
            says what counts as corrupt for that format).
    """
    path = Path(path)
    ext = path.suffix.lstrip(".").lower()
    if ext in _TEXT_PARSERS:
        text = path.read_text(encoding="utf-8")
        try:
            return _TEXT_PARSERS[ext](text)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    if ext in _BYTES_PARSERS:
        data = path.read_bytes()
        try:
            return _BYTES_PARSERS[ext](data)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    allowed = ", ".join("." + e for e in sorted({*_TEXT_PARSERS, *_BYTES_PARSERS}))
    raise ValueError(
        f"{path}: unrecognized references file type '.{ext or '?'}' (expected one of: {allowed})"
    )
