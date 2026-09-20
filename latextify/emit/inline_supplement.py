"""Boundary handling for a main manuscript and SI stored in one Word file."""

from __future__ import annotations

import re

BOUNDARY_MARKER = "%%LATEXTIFY_INLINE_SUPPLEMENT%%"

_HEADING_RE = re.compile(
    r"\\(?P<level>(?:sub)*section)\*?\{(?P<title>"
    r"supplement(?:ary|al)?\s+(?:material|information)|supporting\s+information"
    r")\}(?:\s*\\label\{(?P<label>[^{}]+)\})?",
    re.IGNORECASE,
)
_BARE_HEADING_RE = re.compile(
    r"(?m)^(?:\\textbf\{)?(?P<title>"
    r"supplement(?:ary|al)?\s+(?:material|information)|supporting\s+information"
    r")(?:\})?\s*$",
    re.IGNORECASE,
)

_BOUNDARY_LATEX = (
    "\\clearpage\n"
    "% Supplementary material begins here.\n"
    "\\setcounter{figure}{0}\n"
    "\\setcounter{table}{0}\n"
    "\\setcounter{equation}{0}\n"
    "\\setcounter{section}{0}\n"
    "\\setcounter{subsection}{0}\n"
    "\\setcounter{subsubsection}{0}\n"
    "\\renewcommand{\\thefigure}{S\\arabic{figure}}\n"
    "\\renewcommand{\\thetable}{S\\arabic{table}}\n"
    "\\renewcommand{\\theequation}{S\\arabic{equation}}\n"
    "\\renewcommand{\\thesection}{S\\arabic{section}}\n"
    "% Avoid S0.1 when the Word SI title is followed directly by Heading 2.\n"
    "\\renewcommand{\\thesubsection}{%\n"
    "  \\ifnum\\value{section}=0 S\\arabic{subsection}%\n"
    "  \\else\\thesection.\\arabic{subsection}\\fi}\n"
    "% Keep Hyperref destinations distinct from the main document.\n"
    "\\renewcommand{\\theHfigure}{S.\\arabic{figure}}\n"
    "\\renewcommand{\\theHtable}{S.\\arabic{table}}\n"
    "\\renewcommand{\\theHequation}{S.\\arabic{equation}}\n"
    "\\renewcommand{\\theHsection}{S.\\arabic{section}}\n"
    "\\renewcommand{\\theHsubsection}{S.subsection.\\arabic{section}.\\arabic{subsection}}\n"
)


def mark_inline_supplement(tex: str) -> str:
    """Replace the first conventional SI heading with a protected marker."""
    matches = [
        match for pattern in (_HEADING_RE, _BARE_HEADING_RE) if (match := pattern.search(tex))
    ]
    match = min(matches, key=lambda item: item.start()) if matches else None
    if match is None:
        raise ValueError(
            "inline supplement was requested, but no heading named Supplementary "
            "Material, Supplementary Information, Supplemental Material, or "
            "Supporting Information was found"
        )
    title = match.group("title")
    # Pandoc appends ``\\label{...}`` directly to a heading. Consume it as
    # part of the match and deliberately reattach it to the replacement,
    # rather than leaving it as an accidental orphan from the old section.
    label = match.groupdict().get("label")
    label_tex = f"\\label{{{label}}}" if label else ""
    replacement = f"{BOUNDARY_MARKER}\n\\section*{{{title}}}{label_tex}"
    return tex[: match.start()] + replacement + tex[match.end() :]


def render_inline_supplement(tex: str, *, columns: str = "same") -> str:
    """Turn the marker into a page break, optional column switch, and S-numbering.

    ``columns="same"`` keeps the main manuscript's layout. ``columns="one"``
    changes to one-column mode at the page boundary, which is the common
    main-two-column/SI-one-column submission shape.
    """
    if columns not in {"same", "one"}:
        raise ValueError("inline supplement columns must be 'same' or 'one'")
    if BOUNDARY_MARKER not in tex:
        return tex
    column_switch = (
        "\\ifdefined\\onecolumngrid\\onecolumngrid\\else\\onecolumn\\fi\n"
        if columns == "one"
        else ""
    )
    boundary = _BOUNDARY_LATEX.replace(
        "% Supplementary material begins here.\n",
        "% Supplementary material begins here.\n" + column_switch,
        1,
    )
    return tex.replace(BOUNDARY_MARKER, boundary.rstrip(), 1)
