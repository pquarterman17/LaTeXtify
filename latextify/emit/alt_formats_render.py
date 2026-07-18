"""Rendering primitives for :mod:`latextify.emit.alt_formats` (items 4-5).

Split out purely to keep both files under the repo's size ceiling -- these
are the writer-target-specific leaf functions (reference-list formatting,
citation/figure marker resolution, page assembly) that ``alt_formats.py``'s
``_export`` orchestrates. See that module's docstring for the overall
design and the round's known simplifications.
"""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable
from pathlib import Path

from latextify.model.emit import EmitWarning
from latextify.model.figure import Figure
from latextify.model.refs import Citation, Name, RefEntry

# Portable figure marker, followed (Markdown case: as its own paragraph;
# HTML case: possibly wrapped in <p>) by an optional leftover "Figure N: ..."
# caption paragraph pandoc left as a separate sibling block (the same
# duplicate-caption shape latextify.emit.anchors's _BARE_FIGURE_RE handles
# for LaTeX -- plant_portable_anchors already unwraps a pandoc-promoted
# Figure block, so that is the ONLY duplicate-caption shape either regex
# below needs to know about).
PORTABLE_FIGURE_MD_RE = re.compile(
    r"%%FIGURE:(?P<num>\d+)%%"
    r"(?:[ \t]*\n[ \t]*\n[ \t]*(?:Figure|Fig\.?)\s*(?P=num)\s*[.:]?.*?(?=\n[ \t]*\n|\Z))?",
    re.IGNORECASE | re.DOTALL,
)
PORTABLE_FIGURE_HTML_RE = re.compile(
    r"(?:<p>\s*)?%%FIGURE:(?P<num>\d+)%%(?:\s*</p>)?"
    r"(?:\s*<p>\s*(?:Figure|Fig\.?)\s*(?P=num)\s*[.:]?.*?</p>)?",
    re.IGNORECASE | re.DOTALL,
)
PORTABLE_CITE_RE = re.compile(r"%%CITE:(\d+)%%")

#: Figure extensions a browser <img> can render directly -- see
#: alt_formats.py's module docstring, "Figure embedding (HTML)" note.
_WEB_EMBED_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


# --------------------------------------------------------------------------- #
# Reference list: reconciled entries -> numbered, human-readable citations
# --------------------------------------------------------------------------- #


def _initials(given: str) -> str:
    return " ".join(f"{part[0].upper()}." for part in given.split() if part)


def _format_name_plain(name: Name) -> str:
    if name.is_literal:
        return name.literal
    if name.given:
        return f"{name.family}, {_initials(name.given)}"
    return name.family or name.literal


def format_reference_text(entry: RefEntry) -> str:
    """One human-readable reference-list line for ``entry``.

    A "raw" (Crossref-unmatched) entry's ``title`` is the entire typed
    reference text (see ``latextify.citations.bib``'s ``_raw_to_bibtex``
    docstring) -- used verbatim rather than re-assembled from empty fields.
    """
    if entry.source == "raw":
        return (entry.title or "").strip() or entry.key
    pieces: list[str] = []
    if entry.authors:
        pieces.append(", ".join(_format_name_plain(a) for a in entry.authors))
    if entry.year:
        pieces.append(f"({entry.year})")
    if entry.title:
        pieces.append(f"{entry.title.rstrip('.')}.")
    tail: list[str] = []
    if entry.container_title:
        tail.append(entry.container_title)
    volume = entry.volume or ""
    if entry.issue:
        volume = f"{volume}({entry.issue})" if volume else f"({entry.issue})"
    if volume:
        tail.append(volume)
    if entry.pages:
        tail.append(entry.pages)
    if tail:
        pieces.append(", ".join(tail) + ".")
    if entry.doi:
        pieces.append(f"https://doi.org/{entry.doi}")
    elif entry.url:
        pieces.append(entry.url)
    return " ".join(p for p in pieces if p).strip() or entry.key


def render_reference_list_html(entries: list[RefEntry]) -> str:
    if not entries:
        return ""
    items = "".join(
        f'<li id="ref-{i}">{html.escape(format_reference_text(e))}</li>'
        for i, e in enumerate(entries, start=1)
    )
    return f'<section id="references"><h2>References</h2><ol>{items}</ol></section>'


def render_reference_list_markdown(entries: list[RefEntry]) -> str:
    if not entries:
        return ""
    lines = (f"{i}. {format_reference_text(e)}" for i, e in enumerate(entries, start=1))
    return "## References\n\n" + "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Citation marker resolution (both the ZZLTXCITE sentinel path and the
# dormant %%CITE:<idx>%% anchor path -- see latextify.emit.anchors, its
# LaTeX-target sibling)
# --------------------------------------------------------------------------- #


def _citation_numbers(citation: Citation, number_by_key: dict[str, int]) -> list[int]:
    seen: list[int] = []
    for key in citation.keys:
        number = number_by_key.get(key)
        if number is not None and number not in seen:
            seen.append(number)
    return seen


def render_html_citation(numbers: list[int]) -> str:
    return "[" + ",".join(f'<a href="#ref-{n}">{n}</a>' for n in numbers) + "]"


def render_markdown_citation(numbers: list[int]) -> str:
    return "[" + ",".join(str(n) for n in numbers) + "]"


def resolve_citation_markers(
    text: str,
    citations: tuple[Citation, ...],
    number_by_key: dict[str, int],
    sentinel_re: re.Pattern[str],
    render: Callable[[list[int]], str],
) -> tuple[str, list[EmitWarning]]:
    """Replace both the sentinel and portable-anchor citation markers in ``text``.

    ``sentinel_re`` is ``latextify.ingest.citation_sentinels.SENTINEL_RE``,
    passed in rather than imported here to keep this module free of the
    citation-sentinel/docx-specific import chain.
    """
    warnings: list[EmitWarning] = []
    by_index = {c.index: c for c in citations}  # ZZLTXCITE<i>ZZ sentinel path
    by_position = {c.index + 1: c for c in citations}  # %%CITE:<idx>%% anchor path (dormant)

    def resolve(citation: Citation | None, what: str, ref: str) -> str:
        numbers = _citation_numbers(citation, number_by_key) if citation is not None else []
        if not numbers:
            warnings.append(
                EmitWarning(message=f"unresolved citation {what} {ref}: no matching reference")
            )
            return "[?]"
        return render(numbers)

    text = sentinel_re.sub(
        lambda m: resolve(by_index.get(int(m.group(1))), "sentinel", m.group(1)), text
    )
    text = PORTABLE_CITE_RE.sub(
        lambda m: resolve(by_position.get(int(m.group(1))), "anchor", m.group(1)), text
    )
    return text, warnings


# --------------------------------------------------------------------------- #
# Figure marker resolution + embedding
# --------------------------------------------------------------------------- #


def resolve_figure_markers(
    text: str,
    pattern: re.Pattern[str],
    figures: tuple[Figure, ...],
    render: Callable[[Figure], str],
) -> tuple[str, list[EmitWarning]]:
    warnings: list[EmitWarning] = []
    by_number = {f.number: f for f in figures}

    def sub(match: re.Match[str]) -> str:
        number = int(match.group("num"))
        figure = by_number.get(number)
        if figure is None:
            warnings.append(
                EmitWarning(
                    message=f"unresolved figure anchor for figure {number}: "
                    "no matching Figure record"
                )
            )
            return f"[UNRESOLVED FIGURE {number}]"
        return render(figure)

    return pattern.sub(sub, text), warnings


def embed_data_uri(path: Path) -> str | None:
    """Base64 ``data:`` URI for a browser-renderable figure, else ``None``.

    See alt_formats.py's "Figure embedding (HTML)" note -- PDF/EPS/TIFF are
    not converted here.
    """
    mime = _WEB_EMBED_MIME.get(path.suffix.lower())
    if mime is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def render_html_figure(figure: Figure, data_uri: str | None) -> str:
    caption = html.escape(figure.caption) if figure.caption else ""
    if data_uri is None:
        body = (
            f"<p><em>[figure {figure.number} ({figure.resolved_path.suffix or 'unknown format'}) "
            "could not be embedded in this export]</em></p>"
        )
    else:
        body = f'<img src="{data_uri}" alt="Figure {figure.number}" />'
    figcaption = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure id="fig-{figure.number}">{body}{figcaption}</figure>'


def render_markdown_figure(figure: Figure, rel_path: str | None) -> str:
    caption = figure.caption or f"Figure {figure.number}"
    if rel_path is None:
        return f"*[figure {figure.number} could not be included: {caption}]*"
    return f"![{caption}]({rel_path})"


# --------------------------------------------------------------------------- #
# Page assembly
# --------------------------------------------------------------------------- #


def assemble_html(body_html: str, reference_list_html: str) -> str:
    if not reference_list_html:
        return body_html
    if "</body>" in body_html:
        return body_html.replace("</body>", reference_list_html + "\n</body>", 1)
    return body_html + "\n" + reference_list_html


def assemble_markdown(body_md: str, reference_list_md: str) -> str:
    if not reference_list_md:
        return body_md
    return body_md.rstrip() + "\n\n" + reference_list_md
