"""Equation-audit intermediate representation (plan item 23).

Frozen dataclasses only (see ``latextify/model/__init__.py``); no I/O here.
Populated by :mod:`latextify.audit.equations`, which walks a source .docx's
raw OMML (``word/document.xml``) for the ground-truth equation count and
order, then pairs each one with pandoc's own converted LaTeX (from the same
docx -> JSON-AST pandoc call the body pipeline uses,
:mod:`latextify.ingest.pandoc`). A dropped, merged, or invented equation
shows up as :attr:`EquationAuditResult.count_mismatch` rather than silently
disappearing or being paired with the wrong LaTeX.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EquationRecord:
    """One equation, in document order, Word source paired with converted LaTeX.

    Attributes:
        index: 0-based document-order index across ALL equations (inline and
            display together), matching the raw ``m:oMath`` walk order.
        display: True for a display equation (wrapped in ``m:oMathPara``),
            False for an inline equation (a bare ``m:oMath`` in running
            text). Derived straight from the XML structure, not from
            pandoc's own classification, so it stays correct even when
            :attr:`latex` is empty because of a count mismatch.
        paragraph_snippet: text of the containing Word paragraph with the
            equation's own runs excluded, truncated for readability -- lets
            a user locate the equation in the source .docx. Empty when no
            enclosing paragraph could be resolved (e.g. pandoc invented an
            extra equation with no raw-XML counterpart; see
            :class:`EquationAuditResult`).
        latex: pandoc's converted LaTeX source for this equation (no ``$``/
            ``\\(\\)``/``\\[\\]`` delimiters). Empty when the equation is a
            genuinely blank placeholder, or when no pandoc-converted
            equation lines up with this index (a count mismatch).
    """

    index: int
    display: bool
    paragraph_snippet: str
    latex: str


@dataclass(frozen=True)
class EquationAuditResult:
    """Full extraction outcome for one .docx: equations plus extraction health.

    Attributes:
        equations: every equation, document order, index ``0..N-1``.
        raw_omml_count: how many ``m:oMath`` elements the direct XML walk
            found (ground truth).
        converted_count: how many ``Math`` AST nodes pandoc's own conversion
            produced for the same document.
    """

    equations: tuple[EquationRecord, ...]
    raw_omml_count: int
    converted_count: int

    @property
    def count_mismatch(self) -> bool:
        """True when pandoc dropped, merged, or invented an equation."""
        return self.raw_omml_count != self.converted_count


@dataclass(frozen=True)
class EquationCompileStatus:
    """Per-equation isolated-probe-compile outcome.

    ``ok`` is False when the equation's own standalone probe document failed
    to compile with Tectonic (an unsupported OMML construct converted to
    invalid LaTeX, for instance); ``message`` carries a short reason (empty
    when ``ok`` is True). Produced by
    :func:`latextify.audit.equations.probe_compile_equations`, only when the
    combined all-equations document failed to compile as a whole -- see that
    function's docstring for the two-tier strategy.
    """

    index: int
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class EquationWriteResult:
    """Where :func:`latextify.audit.equations.write_equation_audit` wrote output.

    Attributes:
        audit_md_path: path to the written ``equations_audit.md``.
        audit_pdf_path: path to the compiled ``audit.pdf``, or ``None`` when
            ``--pdf`` was not requested.
        result: the underlying extraction outcome (equations + count health).
        compile_statuses: per-equation compile outcomes; empty when
            ``--pdf`` was not requested or the combined document compiled
            cleanly on the first attempt (every equation is then implicitly
            OK -- no isolated probing was needed).
    """

    audit_md_path: Path
    audit_pdf_path: Path | None
    result: EquationAuditResult
    compile_statuses: tuple[EquationCompileStatus, ...] = ()
