"""Intermediate representation shared by all pipeline stages.

Frozen dataclasses only — no behavior, no I/O. Every stage consumes and
produces these types so stages stay independently testable.

Planned types (plan items 2-9 populate them):
    Document, Section          -- structured body content
    Figure                     -- number, caption, embedded path, override path
    Table, Equation            -- normalized content blocks
    Citation                   -- in-text anchor -> list of citation keys
    RefEntry                   -- one bibliography entry (CSL-shaped fields)

Implemented so far:
    body.py      -- BodyConversionResult, FilterFinding (plan item 3)
    compile.py   -- CompileDiagnostic, CompileResult (plan item 6)
    meta.py      -- Affiliation, Author, Meta paper.yaml/template IR (items 4+8)
    preflight.py -- PreflightFinding, PreflightReport, StyleInventory (plan item 2)
"""

from latextify.model.body import BodyConversionResult, FilterFinding
from latextify.model.compile import CompileDiagnostic, CompileResult, DiagnosticSeverity
from latextify.model.meta import Affiliation, Author, Meta
from latextify.model.preflight import (
    Location,
    PreflightFinding,
    PreflightReport,
    Severity,
    StyleInventory,
)

__all__ = [
    "Affiliation",
    "Author",
    "BodyConversionResult",
    "CompileDiagnostic",
    "CompileResult",
    "DiagnosticSeverity",
    "FilterFinding",
    "Location",
    "Meta",
    "PreflightFinding",
    "PreflightReport",
    "Severity",
    "StyleInventory",
]
