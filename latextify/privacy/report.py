"""Shared vocabulary for metadata inspection and sanitizing.

Every format handler in this package speaks in :class:`Finding` objects, so
the CLI, the GUI, and the tests can render any format's result the same way
without knowing what a ``docProps/core.xml`` or an ``/Info`` dictionary is.

A finding answers three questions, and the third is the one that matters:

    what was found  →  ``category`` + ``summary``
    where           →  ``location``
    why you care    →  ``detail``

The ``detail`` text is not decoration. A report that says "custom.xml
present" tells a user nothing; one that says "custom document properties
can carry the template's original author and internal project codes" tells
them whether to act. Handlers must fill it in.

``removable`` separates the two honest outcomes: things sanitizing will
actually remove, versus things we can only warn about (a legacy fast-save
fragment, text under a redaction rectangle). Reporting an unfixable leak as
though it were fixed is the one failure mode this package must never have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Ordered worst-first so reports sort predictably.
SEVERITIES = ("high", "medium", "low")

_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Finding:
    """One piece of metadata or hidden content found in a file."""

    category: str
    """Stable machine-readable slug, e.g. ``"gps"``, ``"embedded-workbook"``."""

    severity: str
    """One of :data:`SEVERITIES`."""

    summary: str
    """One line naming what was found."""

    detail: str
    """Why it matters -- the sentence that makes the finding actionable."""

    location: str = ""
    """Where it lives: archive member, page number, OLE stream, EXIF tag."""

    count: int = 1
    """How many instances (slides, tags, revisions)."""

    removable: bool = True
    """Whether :func:`~latextify.privacy.registry.sanitize_file` removes it.

    ``False`` means inspection can see it but sanitizing cannot fix it; the
    report must say so rather than implying a clean result.
    """

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY_RANK:
            raise ValueError(f"unknown severity {self.severity!r}; expected one of {SEVERITIES}")

    @property
    def sort_key(self) -> tuple[int, str, str]:
        return (_SEVERITY_RANK[self.severity], self.category, self.location)


@dataclass
class InspectReport:
    """Result of a non-destructive inspection."""

    path: str
    file_format: str
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def sorted_findings(self) -> list[Finding]:
        """Findings worst-first, so a truncated view still shows what matters."""
        return sorted(self.findings, key=lambda f: f.sort_key)

    @property
    def is_clean(self) -> bool:
        return not self.findings

    @property
    def unremovable(self) -> list[Finding]:
        """Findings sanitizing cannot fix -- the ones needing a human decision."""
        return [f for f in self.findings if not f.removable]

    def count_by_severity(self) -> dict[str, int]:
        counts = dict.fromkeys(SEVERITIES, 0)
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts


@dataclass
class SanitizeReport:
    """Result of writing a cleaned copy.

    ``removed`` is what actually came out. ``warnings`` is what the user still
    has to deal with -- residual risk the rewrite could not address, carried
    forward from the unremovable findings.
    """

    path: str
    dest: str
    file_format: str
    removed: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.removed.append(finding)

    def sorted_removed(self) -> list[Finding]:
        return sorted(self.removed, key=lambda f: f.sort_key)

    @property
    def total_removed(self) -> int:
        """Total instances removed, counting each finding's ``count``."""
        return sum(f.count for f in self.removed)
