"""Consolidated per-run conversion report (report.md in the output tree).

Every stage contributes findings; this module owns aggregation and
rendering. The report is the product's honesty layer: the quality bar is
"compiles cleanly + punch list", not silent camera-ready claims.

Sections (plan item 16):
    - preflight findings (unsupported constructs, style problems)
    - citation extraction (source per reference, confidence scores,
      "verify me" flags for low-confidence Crossref matches)
    - figures (overridden vs embedded, conversion notes)
    - compile diagnostics (parsed Tectonic errors/warnings)
"""
