"""Preflight IR: what a source .docx contains before any conversion happens.

Populated by `latextify.ingest.preflight` detectors. Frozen dataclasses only
(see `latextify/model/__init__.py`); this module has no I/O of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How much a finding should worry the user.

    ERROR   -- content will likely be lost or silently mangled by the
               pandoc conversion; must be resolved (or accepted) by hand.
    WARN    -- degraded fidelity or a heuristic guess; worth a manual look.
    INFO    -- informational only, no action implied.
    """

    ERROR = "error"
    WARN = "warn"
    INFO = "info"


@dataclass(frozen=True)
class Location:
    """Where in the document a finding was observed."""

    paragraph_index: int
    text_snippet: str


@dataclass(frozen=True)
class PreflightFinding:
    """One unsupported-or-suspicious construct detected in the source .docx."""

    severity: Severity
    detector: str
    location: Location
    message: str


@dataclass(frozen=True)
class StyleInventory:
    """Which structural Word styles the document actually uses."""

    heading_levels_used: frozenset[int]
    title_style_used: bool
    caption_style_used: bool


@dataclass(frozen=True)
class PreflightReport:
    """Full preflight result for one .docx: findings plus the style inventory."""

    findings: tuple[PreflightFinding, ...]
    styles: StyleInventory

    @property
    def errors(self) -> tuple[PreflightFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[PreflightFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARN)

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)
