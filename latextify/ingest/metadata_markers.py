"""Text markers shared by the author/affiliation and abstract/keyword heuristics.

Split out of :mod:`latextify.ingest.metadata_guess` (2026-08-10). These four
compiled regexes are the "shared constant in the middle" that would otherwise
have blocked a clean split into :mod:`latextify.ingest.metadata_authors` and
:mod:`latextify.ingest.metadata_body`:
:func:`~latextify.ingest.metadata_authors.guess_affiliations` has to stop
consuming affiliation paragraphs at the SAME "Abstract" heading or
"Keywords:" line that :mod:`latextify.ingest.metadata_body` scans forward
from to find those fields, and has to skip the SAME corresponding-author
email line that
:func:`~latextify.ingest.metadata_body.find_corresponding_email` later
searches for. Neither module owns the other, so the patterns live here
instead of inside either one -- a plain module of constants, not a
dataclass, but the identical "one shared thing prevents every other function
from moving" problem.
"""

from __future__ import annotations

import re

# An "Abstract" heading, possibly labeled with trailing punctuation. Real
# manuscripts write "Abstract", "ABSTRACT:", "Abstract.", "Abstract —", etc.
# (the strict "^abstract$" missed "ABSTRACT:" and left the abstract empty in
# paper.yaml AND unstripped from the body). Trailing text after the label
# ("Abstract: Using depth...") is intentionally NOT matched here -- that
# inline-abstract shape is a separate case.
ABSTRACT_HEADING_RE = re.compile(r"^abstract\s*[:.–—-]?\s*$", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^(?:keywords|key\s*words)\s*[:.]\s*(.*)$", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[\w-]+")
CORRESPONDING_RE = re.compile(r"correspond", re.IGNORECASE)
