"""Submission-shaping emit options: per-document layout, anonymize, floats-at-end.

Three opt-in ways to reshape an emitted document for a submission workflow
(plan GUI_OPTIONS_FORMATS items 6-8), all applied to the *rendered* preamble
text so no journal template needs to know about them:

- :class:`DocumentLayout` — per-document column mode, reviewer line numbers,
  and double spacing. Column knowledge is keyed by **document class**, not
  journal: every APS/AIP journal shares ``revtex4-2``, whose blessed switch is
  ``preprint``/``reprint``; any other class takes the standard LaTeX
  ``onecolumn``/``twocolumn`` options. Line numbers use REVTeX's native
  ``linenumbers`` class option where available and the ``lineno`` package
  elsewhere; double spacing always uses ``setspace``.
- :func:`anonymize_meta` / :func:`strip_acknowledgments` — double-blind
  submission: placeholder author block, no affiliations, and the
  acknowledgments section/environment removed from the body.
- ``figures_at_end`` (an argument of the preamble builders) — ``endfloat``
  moves figure/table floats after the references, as several publishers
  require at submission.

This module also owns the generic preamble post-processing that
``emit/project.py`` historically applied (hyperref + raggedbottom insurance
and the plain-article supplement preamble) — the builders here compose all of
it in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from latextify.model.meta import Author, Meta
from latextify.templates.loader import FigureEnv, Journal

# --------------------------------------------------------------------------- #
# Per-document layout
# --------------------------------------------------------------------------- #

#: (add, remove) class-option tuples per forced column mode, keyed by document
#: class. Classes absent here use the standard LaTeX option names.
_COLUMN_OPTIONS: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "revtex4-2": {
        "one": (("preprint",), ("reprint",)),
        "two": (("reprint",), ("preprint",)),
    },
}
_GENERIC_COLUMN_OPTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "one": (("onecolumn",), ("twocolumn",)),
    "two": (("twocolumn",), ("onecolumn",)),
}

#: Classes with a native line-numbers class option (preferred over lineno).
_LINENO_CLASS_OPTIONS: dict[str, tuple[str, ...]] = {"revtex4-2": ("linenumbers",)}

_LINENO_PACKAGE = (
    "% Reviewer line numbers (per-document layout option).\n"
    "\\usepackage{lineno}\n\\linenumbers\n"
)
_DOUBLESPACE_PACKAGE = (
    "% Double spacing (per-document layout option). setspace's \\doublespacing\n"
    "% dispatches on the standard classes' \\@ptsize internal, which REVTeX-family\n"
    "% classes never define -- default it to the 10pt table first (verified by\n"
    "% compiling: bare setspace under revtex4-2 dies with an undefined \\@ptsize).\n"
    "\\makeatletter\\@ifundefined{@ptsize}{\\def\\@ptsize{0}}{}\\makeatother\n"
    "\\usepackage{setspace}\n\\doublespacing\n"
)
_ENDFLOAT_PACKAGE = (
    "% Figures & tables gathered after the references (figures-at-end option).\n"
    "\\usepackage[nolists,tablesfirst]{endfloat}\n"
)
#: Classes with a native floats-at-end class option. The endfloat package is
#: incompatible with REVTeX (its end-of-document code calls \onecolumn, which
#: REVTeX does not define); REVTeX's own `endfloats` option does the job.
_FIGSEND_CLASS_OPTIONS: dict[str, tuple[str, ...]] = {"revtex4-2": ("endfloats",)}

_DOCUMENTCLASS_RE = re.compile(r"\\documentclass(?:\[(?P<opts>[^\]]*)\])?\{(?P<cls>[^}]+)\}")


@dataclass(frozen=True)
class DocumentLayout:
    """Layout overrides for one emitted document (main or supplement).

    ``columns`` is ``None`` (journal default), ``"one"``, or ``"two"``.
    """

    columns: str | None = None
    line_numbers: bool = False
    double_spacing: bool = False

    def is_default(self) -> bool:
        return self.columns is None and not self.line_numbers and not self.double_spacing


def parse_layout_form(
    columns: str, line_numbers: bool, double_spacing: bool
) -> DocumentLayout | None:
    """Build a :class:`DocumentLayout` from GUI form values; ``None`` if all default.

    Raises ``ValueError`` naming the field for an unknown ``columns`` value
    (the API layer translates that to a 400).
    """
    if columns not in ("default", "one", "two"):
        raise ValueError(f"columns must be 'default', 'one', or 'two' (got {columns!r})")
    layout = DocumentLayout(
        columns=None if columns == "default" else columns,
        line_numbers=line_numbers,
        double_spacing=double_spacing,
    )
    return None if layout.is_default() else layout


def _append(preamble_text: str, block: str) -> str:
    if not preamble_text.endswith("\n"):
        preamble_text += "\n"
    return preamble_text + block


def _rewrite_class_options(
    preamble_text: str, *, add: tuple[str, ...], remove: tuple[str, ...]
) -> str:
    """Add/remove options on the first ``\\documentclass`` line of the preamble."""

    def _swap(match: re.Match[str]) -> str:
        opts = [o.strip() for o in (match.group("opts") or "").split(",") if o.strip()]
        opts = [o for o in opts if o not in remove and o not in add]
        opts.extend(add)
        bracket = f"[{','.join(opts)}]" if opts else ""
        return f"\\documentclass{bracket}{{{match.group('cls')}}}"

    return _DOCUMENTCLASS_RE.sub(_swap, preamble_text, count=1)


def _apply_figures_at_end(preamble_text: str, *, document_class: str) -> str:
    """Gather floats after the references, via the class-appropriate mechanism."""
    native = _FIGSEND_CLASS_OPTIONS.get(document_class)
    if native:
        return _rewrite_class_options(preamble_text, add=native, remove=())
    return _append(preamble_text, _ENDFLOAT_PACKAGE)


def apply_document_layout(
    preamble_text: str, *, document_class: str, layout: DocumentLayout | None
) -> str:
    """Apply a per-document layout to a rendered preamble; no-op for ``None``."""
    if layout is None or layout.is_default():
        return preamble_text
    if layout.columns is not None:
        table = _COLUMN_OPTIONS.get(document_class, _GENERIC_COLUMN_OPTIONS)
        add, remove = table[layout.columns]
        preamble_text = _rewrite_class_options(preamble_text, add=add, remove=remove)
    if layout.line_numbers:
        native = _LINENO_CLASS_OPTIONS.get(document_class)
        if native:
            preamble_text = _rewrite_class_options(preamble_text, add=native, remove=())
        else:
            preamble_text = _append(preamble_text, _LINENO_PACKAGE)
    if layout.double_spacing:
        preamble_text = _append(preamble_text, _DOUBLESPACE_PACKAGE)
    return preamble_text


# --------------------------------------------------------------------------- #
# Preamble builders (compose layout + the generic post-processing insurance)
# --------------------------------------------------------------------------- #

_HYPERREF_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{hyperref\}")
_DEFAULT_HYPERREF_LINE = (
    "\\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}\n"
)

# A journal preamble that already fixes a bottom mode (either direction) is left
# alone; otherwise \raggedbottom is appended (see _ensure_raggedbottom).
_BOTTOM_MODE_RE = re.compile(r"\\(?:ragged|flush)bottom\b")
_RAGGEDBOTTOM_LINE = (
    "% Let each column end at its natural length. Two-column 'reprint' classes\n"
    "% (REVTeX reprint, IEEEtran, elsarticle twocolumn) default to \\flushbottom,\n"
    "% which stretches inter-paragraph glue to equalize column height -- opening a\n"
    "% large gap on a text-only page whose figures floated elsewhere. Add\n"
    "% \\flushbottom after this \\input in main.tex for the published look.\n"
    "\\raggedbottom\n"
)

# One-column plain-article supplement (--supplement-onecolumn): a deliberately
# simple document class for the "less strict" SI format many journals accept.
# 11pt article + natbib with a PORTABLE numeric bibstyle -- the journal's own
# apsrev4-2/aipnum4-2 are REVTeX-specific and do not compile under article, and
# an SI shares references.bib, so unsrtnat (bundled, natbib-native) renders the
# cited subset without pulling in the journal machinery. S-numbering is appended
# exactly like the journal-class path (in emit/project.py).
_PLAIN_ARTICLE_SUPPLEMENT_PREAMBLE = (
    "% One-column plain-article supplement (--supplement-onecolumn).\n"
    "\\documentclass[11pt]{article}\n"
    "\\usepackage{amsmath}\n"
    "\\usepackage{amssymb}\n"
    "\\usepackage{graphicx}\n"
    "\\usepackage{bm}\n"
    "\\usepackage{booktabs}\n"
    "\\usepackage[numbers]{natbib}\n"
    "\\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}\n"
    "\\bibliographystyle{unsrtnat}\n"
)
# A one-column document has no page-width float, so a wide figure falls back to
# the ordinary single-column figure environment (figure* is a two-column-only
# construct).
_ONECOLUMN_FIGURE_ENV = FigureEnv(single="figure", wide="figure")


def _ensure_hyperref(preamble_text: str) -> str:
    """Append hyperref wiring if the journal's own preamble doesn't already load it."""
    if _HYPERREF_RE.search(preamble_text):
        return preamble_text
    return _append(preamble_text, _DEFAULT_HYPERREF_LINE)


def _ensure_raggedbottom(preamble_text: str) -> str:
    """Append ``\\raggedbottom`` unless the journal preamble already sets a bottom mode.

    Two-column "reprint" classes force every column to equal height with
    ``\\flushbottom``; on a text-only page (its figures floated to another page)
    the only way to reach full height is to inflate the rubber glue between
    paragraphs, which reads as a jarring mid-column gap. ``\\raggedbottom`` lets
    the column end where the text ends. Harmless for single-column classes
    (already their default), so it is applied journal-agnostically -- one fix for
    every two-column journal rather than per-template. A preamble that already
    commits to ``\\raggedbottom``/``\\flushbottom`` is respected.
    """
    if _BOTTOM_MODE_RE.search(preamble_text):
        return preamble_text
    return _append(preamble_text, _RAGGEDBOTTOM_LINE)


def build_main_preamble(
    rendered: str,
    *,
    document_class: str,
    layout: DocumentLayout | None = None,
    figures_at_end: bool = False,
) -> str:
    """Rendered journal preamble -> final main-document preamble text."""
    text = apply_document_layout(
        _ensure_hyperref(rendered), document_class=document_class, layout=layout
    )
    if figures_at_end:
        text = _apply_figures_at_end(text, document_class=document_class)
    return _ensure_raggedbottom(text)


def build_supplement_preamble(
    journal: Journal,
    citation_style: str | None,
    *,
    onecolumn: bool,
    layout: DocumentLayout | None = None,
    figures_at_end: bool = False,
) -> str:
    """Supplement preamble: plain-article (onecolumn) or the journal's class.

    On the plain-article path the ``columns`` choice is already spent (that IS
    the one-column format), so only line numbers / double spacing apply.
    """
    document_class = "article" if onecolumn else journal.document_class
    if onecolumn:
        effective = replace(layout, columns=None) if layout is not None else None
        text = apply_document_layout(
            _PLAIN_ARTICLE_SUPPLEMENT_PREAMBLE, document_class=document_class, layout=effective
        )
    else:
        text = apply_document_layout(
            _ensure_hyperref(journal.render_preamble(mode=citation_style)),
            document_class=document_class,
            layout=layout,
        )
    if figures_at_end:
        text = _apply_figures_at_end(text, document_class=document_class)
    return _ensure_raggedbottom(text)


# --------------------------------------------------------------------------- #
# Double-blind anonymization
# --------------------------------------------------------------------------- #

#: Placeholder author block for double-blind submissions. Many classes error
#: with no author at all, so a placeholder is safer than an empty block.
_ANONYMOUS_AUTHOR = Author(name="Anonymous Author(s)")

# \section{Acknowledgments} (any spelling/starred form) up to the next
# structural command, or the REVTeX acknowledgments environment.
_ACK_SECTION_RE = re.compile(
    r"(?ms)^\\section\*?\{Acknowledg[^}]*\}\s*?\n.*?"
    r"(?=^\\(?:section|subsection|appendix|input|bibliography)|\Z)"
)
_ACK_ENV_RE = re.compile(r"(?s)[ \t]*\\begin\{acknowledgments?\}.*?\\end\{acknowledgments?\}\n?")


def anonymize_meta(meta: Meta) -> Meta:
    """A double-blind copy of ``meta``: placeholder author, no affiliations.

    Title, abstract, and keywords are kept -- they are part of the reviewed
    content, not the identity block.
    """
    return replace(meta, authors=(_ANONYMOUS_AUTHOR,), affiliations=())


def strip_acknowledgments(body_tex: str) -> tuple[str, bool]:
    """Remove an acknowledgments section/environment from an emitted body.

    Returns ``(new_body, removed)`` -- ``removed`` tells the caller to note in
    report.md that identifying content was dropped for double-blind review.
    """
    stripped, n_env = _ACK_ENV_RE.subn("", body_tex)
    stripped, n_sec = _ACK_SECTION_RE.subn("", stripped)
    return stripped, bool(n_env or n_sec)
