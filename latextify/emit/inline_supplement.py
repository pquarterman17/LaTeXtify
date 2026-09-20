"""Boundary handling for a main manuscript and SI stored in one Word file."""

from __future__ import annotations

import re

BOUNDARY_MARKER = "%%LATEXTIFY_INLINE_SUPPLEMENT%%"

_HEADING_RE = re.compile(
    r"\\(?P<level>(?:sub)*section)\*?\{(?P<title>"
    r"supplement(?:ary|al)?\s+(?:material|information)|supporting\s+information"
    r")\}",
    re.IGNORECASE,
)

_BOUNDARY_LATEX = (
    "\\clearpage\n"
    "% Supplementary material begins here.\n"
    "\\setcounter{figure}{0}\n"
    "\\setcounter{table}{0}\n"
    "\\setcounter{equation}{0}\n"
    "\\setcounter{section}{0}\n"
    "\\renewcommand{\\thefigure}{S\\arabic{figure}}\n"
    "\\renewcommand{\\thetable}{S\\arabic{table}}\n"
    "\\renewcommand{\\theequation}{S\\arabic{equation}}\n"
    "\\renewcommand{\\thesection}{S\\arabic{section}}\n"
)


def mark_inline_supplement(tex: str) -> str:
    """Replace the first conventional SI heading with a protected marker."""
    match = _HEADING_RE.search(tex)
    if match is None:
        raise ValueError(
            "inline supplement was requested, but no heading named Supplementary "
            "Material, Supplementary Information, Supplemental Material, or "
            "Supporting Information was found"
        )
    title = match.group("title")
    replacement = f"{BOUNDARY_MARKER}\n\\section*{{{title}}}"
    return tex[: match.start()] + replacement + tex[match.end() :]


def render_inline_supplement(tex: str) -> str:
    """Turn the protected marker into a page break and S-numbering reset."""
    if BOUNDARY_MARKER not in tex:
        return tex
    return tex.replace(BOUNDARY_MARKER, _BOUNDARY_LATEX.rstrip(), 1)
