"""Compile-stage IR: structured diagnostics and results from the Tectonic wrapper.

Frozen dataclasses only, per the model/ contract (see model/__init__.py) --
no I/O, no behavior. `latextify.compile.logs` produces `CompileDiagnostic`
instances from raw TeX/Tectonic log text; `latextify.compile.tectonic`
produces the overall `CompileResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._compat import StrEnum


class DiagnosticSeverity(StrEnum):
    """Severity of a single compile diagnostic."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class CompileDiagnostic:
    """One structured finding extracted from a TeX/Tectonic compile log.

    `file` and `line` are `None` when the underlying log line did not carry
    a location (e.g. a package-level warning with no line reference).
    """

    severity: DiagnosticSeverity
    message: str
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class CompileResult:
    """Outcome of one `tectonic -X compile` invocation."""

    success: bool
    pdf_path: Path | None
    diagnostics: tuple[CompileDiagnostic, ...]
    raw_log: str
    returncode: int

    @property
    def errors(self) -> tuple[CompileDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is DiagnosticSeverity.ERROR)

    @property
    def warnings(self) -> tuple[CompileDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is DiagnosticSeverity.WARNING)
