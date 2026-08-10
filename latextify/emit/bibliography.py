"""The ``\\bibliography`` inclusion contract shared by main.tex and supplement.tex.

Plan item 26: whether a document gets a real ``\\bibliography{references}``
line or a self-explaining comment instead lives in a *regenerated* file
(``generated/bibliography.tex`` / ``generated/supplement_bibliography.tex``),
never directly in the write-once ``main.tex``/``supplement.tex``. That is what
lets a citation-free manuscript compile under classes -- IEEEtran chief among
them -- whose ``\\thebibliography`` redefinition errors on an empty reference
list ("Something's wrong -- perhaps a missing \\item").

Both :mod:`latextify.emit.project` (the main document) and
:mod:`latextify.emit.supplement` (plan item 21's second document) make this
exact same choice, once each, on their own ``\\cite{`` state -- hence a module
of its own rather than a copy in each: ``project`` importing from
``supplement`` (or the reverse) to share one constant would be circular, since
``project`` is also the one that calls ``supplement.emit_supplement``.

:func:`legacy_bibliography_warning` lives here too because it exists only to
advise migrating a pre-item-26 ``main.tex`` onto the ``BIBLIOGRAPHY_LINE`` /
``BIBLIOGRAPHY_EMPTY`` contract above -- it has no reason to exist anywhere
else.
"""

from __future__ import annotations

import re
from pathlib import Path

from latextify.model.emit import EmitWarning

#: A citation-bearing document \input's this line from its regenerated
#: bibliography file.
BIBLIOGRAPHY_LINE = "\\bibliography{references}\n"

#: A citation-free document \input's this comment instead of an empty
#: ``\bibliography`` line -- regenerated every run, so a
#: ``\bibliography{references}`` line reappears here automatically once
#: citations are found.
BIBLIOGRAPHY_EMPTY = (
    "% This manuscript has no citations, so no \\bibliography line is emitted.\n"
    "% Regenerated every run: a \\bibliography{references} line reappears here\n"
    "% automatically once citations are found. Emitting an empty \\bibliography\n"
    "% makes some classes -- notably IEEEtran -- error at \\end{thebibliography}.\n"
)

# A pre-item-26 main.tex called ``\bibliography`` directly. main.tex is
# user-owned/write-once so we cannot rewrite it; detect the legacy line (not
# commented out, and distinct from the new ``\input{generated/bibliography}``)
# to advise the one-line migration instead.
_DIRECT_BIBLIOGRAPHY_RE = re.compile(r"(?m)^[^%\n]*\\bibliography\{")


def legacy_bibliography_warning(main_tex_path: Path) -> list[EmitWarning]:
    """Advise migrating a pre-item-26 ``main.tex`` off its direct ``\\bibliography`` call.

    New projects ``\\input{generated/bibliography}`` so a citation-free
    manuscript emits no ``\\bibliography`` line and still compiles under
    IEEEtran (plan item 26). A ``main.tex`` written before that change is
    user-owned and write-once -- it still carries the direct
    ``\\bibliography{references}`` line, which breaks citation-free IEEEtran
    compiles -- so surface a one-line-edit warning rather than silently
    leaving it broken. Returns no warning once the file has been migrated (it
    then contains the ``\\input{generated/bibliography}`` include).
    """
    try:
        existing = main_tex_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if "\\input{generated/bibliography}" in existing:
        return []
    if _DIRECT_BIBLIOGRAPHY_RE.search(existing):
        return [
            EmitWarning(
                message=(
                    "main.tex calls \\bibliography{references} directly; new projects "
                    "\\input{generated/bibliography} instead so citation-free manuscripts "
                    "compile (an empty \\bibliography breaks IEEEtran). Replace the "
                    "\\bibliography{references} line in main.tex with "
                    "\\input{generated/bibliography}."
                )
            )
        ]
    return []
