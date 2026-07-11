"""Intermediate representation shared by all pipeline stages.

Frozen dataclasses only — no behavior, no I/O. Every stage consumes and
produces these types so stages stay independently testable.

Planned types (plan item 5 populates them):
    Document, Section          -- structured body content
    Table, Equation            -- normalized content blocks

Implemented so far:
    body.py      -- BodyConversionResult, FilterFinding (plan item 3)
    compile.py   -- CompileDiagnostic, CompileResult (plan item 6)
    emit.py      -- EmitResult, EmitWarning (plan item 5)
    figure.py    -- Figure, FigureSource (plan item 9)
    meta.py      -- Affiliation, Author, Meta paper.yaml/template IR (items 4+8)
    preflight.py -- PreflightFinding, PreflightReport, StyleInventory (plan item 2)
    refs.py      -- RefEntry, Citation, Name bibliography IR (plan item 7)
"""

from latextify.model.body import BodyConversionResult, FilterFinding
from latextify.model.compile import CompileDiagnostic, CompileResult, DiagnosticSeverity
from latextify.model.emit import EmitResult, EmitWarning
from latextify.model.figure import Figure, FigureSource
from latextify.model.meta import Affiliation, Author, Meta
from latextify.model.preflight import (
    Location,
    PreflightFinding,
    PreflightReport,
    Severity,
    StyleInventory,
)
from latextify.model.refs import Citation, Name, RefEntry

__all__ = [
    "Affiliation",
    "Author",
    "BodyConversionResult",
    "Citation",
    "CompileDiagnostic",
    "CompileResult",
    "DiagnosticSeverity",
    "EmitResult",
    "EmitWarning",
    "Figure",
    "FigureSource",
    "FilterFinding",
    "Location",
    "Meta",
    "Name",
    "PreflightFinding",
    "PreflightReport",
    "RefEntry",
    "Severity",
    "StyleInventory",
]
