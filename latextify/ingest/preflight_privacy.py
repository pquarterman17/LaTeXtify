"""Fold a `.docx`'s privacy findings into the preflight report.

METADATA_PRIVACY_PLAN item 15: before this module existed, whether a `.docx`
carried tracked changes, review comments, or an author trail was only ever
visible by running `latextify inspect` as its own, separate step -- and
nobody runs a step they don't know exists. `latextify.ingest.preflight`
already inventories a manuscript for constructs pandoc will mangle before any
conversion happens; this module rides along on that same pass so the same
information reaches the same `report.md` a normal `latextify convert` already
produces, with no extra step for the user to remember.

The detection itself is not reimplemented here -- `latextify.privacy.
docx_adapter.inspect` already reads `word/document.xml`, the WordprocessingML
header/footer/footnote/endnote parts, `comments.xml`, `people.xml`,
`settings.xml` and `docProps/*` for exactly this, and is the same code path
`latextify inspect`/`clean` use. This module only translates its `Finding`
objects (`latextify.privacy.report`) into
`latextify.model.preflight.PreflightFinding` so they render through the
*same* "## Preflight Findings" section as every other detector, instead of
inventing a second warning vocabulary.

Two things distinguish these findings from the rest of preflight's:

    * They are never `Severity.ERROR`. An author's name, a save date, or an
      open review thread is completely normal in a manuscript and must never
      fail or block a conversion that would otherwise succeed -- this is a
      "you may want to know" surface, not a gate. `Finding.severity`
      ("high"/"medium"/"low") maps to WARN/WARN/INFO, so a tracked change,
      hidden text, a cached reviewer list, or an open comment thread
      (high/medium) stays visible while a quieter signal like an rsid index
      or a save date (low) does not compete for the reader's attention.
    * They carry no real paragraph location -- docx_adapter inspects whole
      package parts (`docProps/core.xml`, `word/comments.xml`, ...), not a
      single spot in the body -- so `Location.paragraph_index` is `-1` (the
      same "not applicable" sentinel `ingest.metadata_guess` already uses),
      and the XML part name goes in `text_snippet` instead of a body excerpt.

Only `.docx` has a privacy adapter today (`latextify.privacy.registry`);
every other manuscript format this module is asked about (`.odt`/`.rtf`/
`.md`, or anything else) returns no findings rather than erroring, matching
how the rest of preflight already treats them (see
`latextify.ingest.formats.is_alt_manuscript_format`). Likewise, if inspection
itself fails for a reason its own defenses did not anticipate (docx_adapter
already tolerates a malformed individual XML part internally; this is the
backstop for anything stranger -- a corrupt zip its own validation missed,
an exhausted resource), that failure becomes a single INFO note rather than
an exception: a privacy nicety must never be able to fail a conversion that
otherwise succeeds.
"""

from __future__ import annotations

from pathlib import Path

from latextify.ingest.formats import is_docx
from latextify.model.preflight import Location, PreflightFinding, Severity
from latextify.privacy import docx_adapter
from latextify.privacy.report import Finding

#: Finding.severity -> preflight Severity. Never ERROR -- see module docstring.
_SEVERITY: dict[str, Severity] = {
    "high": Severity.WARN,
    "medium": Severity.WARN,
    "low": Severity.INFO,
}


def _translate(finding: Finding) -> PreflightFinding:
    return PreflightFinding(
        severity=_SEVERITY[finding.severity],
        detector=f"privacy_{finding.category}",
        location=Location(paragraph_index=-1, text_snippet=finding.location),
        message=f"{finding.summary}. {finding.detail}",
    )


def privacy_findings(docx_path: str | Path) -> tuple[PreflightFinding, ...]:
    """`docx_adapter.inspect(docx_path)`'s findings, as `PreflightFinding`s.

    Returns `()` for anything that is not a `.docx` (no adapter exists for
    the other manuscript formats) and for a `.docx` whose inspection raises
    for any reason. The latter also appends one INFO note naming the failure,
    so a skipped check is visible in `report.md` rather than just absent.
    """
    path = Path(docx_path)
    if not is_docx(path):
        return ()
    try:
        findings, _warnings = docx_adapter.inspect(path)
    except Exception as exc:
        return (
            PreflightFinding(
                severity=Severity.INFO,
                detector="privacy_check_failed",
                location=Location(paragraph_index=-1, text_snippet=""),
                message=(
                    "Could not check this document for tracked changes, comments, or "
                    f"other authoring metadata ({exc}); run `latextify inspect` on it "
                    "directly to see what it carries."
                ),
            ),
        )
    return tuple(_translate(f) for f in findings)
