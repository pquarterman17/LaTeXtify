"""docx paragraph extraction: ``word/document.xml`` -> a list of :class:`Para`.

Split out of :mod:`latextify.ingest.metadata_guess` (2026-08-10), which sits
at its own line-count ratchet pin (``tests/test_repo_integrity.py``). This is
the lowest layer of the metadata-guessing stack: it knows nothing about
titles, authors, or abstracts -- it only turns the first ``limit`` top-level
``<w:p>`` paragraphs of the manuscript body into a list of :class:`Para`,
each carrying its style ID, its text broken into superscript/non-superscript
:class:`Segment` runs, and the largest run font size seen in the paragraph
(half-points, straight from ``<w:sz w:val="...">``). Every heuristic in
:mod:`latextify.ingest.metadata_authors` and
:mod:`latextify.ingest.metadata_body` consumes this list; none of them touch
the XML directly.

Superscript detection (``<w:vertAlign w:val="superscript">``) matters because
affiliation markers and corresponding-author symbols are typed as superscript
runs on the author line -- see :mod:`latextify.ingest.metadata_authors` for
what happens to them. Font size matters because a manuscript with no Title
style still usually has its title in the largest font among the first few
paragraphs, which is :func:`~latextify.ingest.metadata_authors.guess_title`'s
fallback.

Uses the same hardened lxml parser as ``preflight.py``
(:func:`latextify.ingest._xml.hardened_xml_parser`) -- this reads an
untrusted, author-supplied .docx.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from latextify.ingest._xml import hardened_xml_parser

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NSMAP = {"w": _W_NS}


@dataclass
class Segment:
    text: str
    superscript: bool


@dataclass
class Para:
    style_id: str | None
    segments: list[Segment] = field(default_factory=list)
    font_size: int | None = None  # max half-point run size seen in this paragraph

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


def _qn(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def read_document_root(docx_path: Path):
    try:
        archive = zipfile.ZipFile(docx_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{docx_path}: not a valid .docx ({exc})") from exc
    with archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError(f"{docx_path}: not a valid .docx (missing word/document.xml)")
        with archive.open("word/document.xml") as fh:
            try:
                return etree.parse(fh, parser=hardened_xml_parser()).getroot()
            except etree.XMLSyntaxError as exc:
                raise ValueError(
                    f"{docx_path}: not a valid .docx (malformed XML in word/document.xml: {exc})"
                ) from exc


def extract_paragraphs(root, limit: int) -> list[Para]:
    body = root.find("w:body", _NSMAP)
    if body is None:
        return []

    paras: list[Para] = []
    for p in body.findall("w:p", _NSMAP):
        if len(paras) >= limit:
            break

        style_el = p.find("w:pPr/w:pStyle", _NSMAP)
        style_id = style_el.get(_qn("val")) if style_el is not None else None

        segments: list[Segment] = []
        max_size: int | None = None
        for run in p.findall("w:r", _NSMAP):
            text = "".join(t.text or "" for t in run.findall("w:t", _NSMAP))
            vert = run.find("w:rPr/w:vertAlign", _NSMAP)
            is_super = vert is not None and vert.get(_qn("val")) == "superscript"
            sz_el = run.find("w:rPr/w:sz", _NSMAP)
            if sz_el is not None:
                try:
                    sz = int(sz_el.get(_qn("val")))
                except (TypeError, ValueError):
                    sz = None
                if sz is not None and (max_size is None or sz > max_size):
                    max_size = sz
            if text:
                segments.append(Segment(text=text, superscript=is_super))

        paras.append(Para(style_id=style_id, segments=segments, font_size=max_size))

    return paras
