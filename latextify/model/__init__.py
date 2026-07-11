"""Intermediate representation shared by all pipeline stages.

Frozen dataclasses only — no behavior, no I/O. Every stage consumes and
produces these types so stages stay independently testable.

Planned types (plan items 2-9 populate them):
    Document, Section          -- structured body content
    Author, Affiliation, Meta  -- title-page metadata (paper.yaml schema)
    Figure                     -- number, caption, embedded path, override path
    Table, Equation            -- normalized content blocks
    Citation                   -- in-text anchor -> list of citation keys
    RefEntry                   -- one bibliography entry (CSL-shaped fields)
    PreflightFinding           -- severity, location, message
"""

# Re-exported for convenience (item 8); item 4 will unify with model/meta.py at merge time.
from latextify.model.meta_sidecar import Author as Author  # noqa: E401
from latextify.model.meta_sidecar import Meta as Meta
