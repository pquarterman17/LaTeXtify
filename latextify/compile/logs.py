"""Parse Tectonic/TeX compile output into structured diagnostics.

Never surface raw TeX log spew to callers -- only lines matching a known
diagnostic shape become a `CompileDiagnostic`; everything else (font
tables, box registers, package version banners, "note: downloading ..."
progress chatter, the Fontconfig warning Tectonic prints on every run) is
silently dropped by construction, since we only ever emit a diagnostic
when a targeted pattern matches.

Two error formats are recognized, in priority order (verified against
real Tectonic 0.16.9 output on Windows):

1. Tectonic's own terse summary line, printed to stderr when a compile
   fails: ``error: <file>:<line>: <message>`` (e.g.
   ``error: main.tex:3: Undefined control sequence``). This is what
   `tectonic.compile_document` normally sees, and it already carries the
   file name, so it is preferred whenever present.
2. The classic TeX log block -- ``! <message>`` followed a few lines later
   by ``l.<line> <context>`` -- which is what's available when only a bare
   ``.log`` file's text is being parsed (no wrapping ``error:`` line
   available). Used as a fallback only when no terse-format errors were
   found, so a normal `compile_document()` run (which concatenates
   stdout+stderr+the .log file into one blob) doesn't double-report the
   same failure once from each format.

Warnings (``LaTeX Warning: ...``, ``Package <name> Warning: ...``,
BibTeX's ``Warning--...``) are always scanned for, independent of which
error format matched.
"""

from __future__ import annotations

import re

from latextify.model.compile import CompileDiagnostic, DiagnosticSeverity

_TERSE_ERROR_RE = re.compile(r"^error: (?P<file>\S+):(?P<line>\d+): (?P<message>.+)$")
_CLASSIC_ERROR_RE = re.compile(r"^! (?P<message>.+)$")
_CLASSIC_LINE_RE = re.compile(r"^l\.(?P<line>\d+)\b")
_LATEX_WARNING_RE = re.compile(
    r"^(?:Package (?P<pkg>\S+) |LaTeX )Warning: (?P<message>.+?)"
    r"(?: on input line (?P<line>\d+))?\.$"
)
_BIBTEX_WARNING_RE = re.compile(r"^Warning--(?P<message>.+)$")

# How many lines past a classic "! message" to look for its "l.N" context line.
_CLASSIC_LOOKAHEAD = 15


def _strip_bangs(message: str) -> str:
    """Tectonic sometimes nests its own "!" with TeX's, e.g. "! ! LaTeX Error: ..."."""
    while message.startswith("! "):
        message = message[2:]
    return message


def _parse_terse_errors(lines: list[str]) -> list[CompileDiagnostic]:
    diagnostics = []
    for line in lines:
        m = _TERSE_ERROR_RE.match(line)
        if m:
            diagnostics.append(
                CompileDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    message=_strip_bangs(m.group("message").strip()),
                    file=m.group("file"),
                    line=int(m.group("line")),
                )
            )
    return diagnostics


def _parse_classic_errors(lines: list[str], default_file: str | None) -> list[CompileDiagnostic]:
    diagnostics = []
    for i, line in enumerate(lines):
        m = _CLASSIC_ERROR_RE.match(line)
        if not m:
            continue
        message = _strip_bangs(m.group("message").strip())
        line_no = None
        for lookahead in lines[i + 1 : i + 1 + _CLASSIC_LOOKAHEAD]:
            line_m = _CLASSIC_LINE_RE.match(lookahead)
            if line_m:
                line_no = int(line_m.group("line"))
                break
        diagnostics.append(
            CompileDiagnostic(
                severity=DiagnosticSeverity.ERROR,
                message=message,
                file=default_file,
                line=line_no,
            )
        )
    return diagnostics


def _parse_warnings(lines: list[str], default_file: str | None) -> list[CompileDiagnostic]:
    diagnostics = []
    for line in lines:
        m = _LATEX_WARNING_RE.match(line)
        if m:
            message = m.group("message").strip()
            pkg = m.group("pkg")
            if pkg:
                message = f"[{pkg}] {message}"
            line_group = m.group("line")
            diagnostics.append(
                CompileDiagnostic(
                    severity=DiagnosticSeverity.WARNING,
                    message=message,
                    file=default_file,
                    line=int(line_group) if line_group else None,
                )
            )
            continue
        m = _BIBTEX_WARNING_RE.match(line)
        if m:
            diagnostics.append(
                CompileDiagnostic(
                    severity=DiagnosticSeverity.WARNING,
                    message=m.group("message").strip(),
                    file=None,
                    line=None,
                )
            )
    return diagnostics


def parse_log(raw_log: str, *, default_file: str | None = None) -> list[CompileDiagnostic]:
    """Extract structured diagnostics from Tectonic/TeX compile output.

    `raw_log` is whatever text was captured -- combined stdout+stderr from
    `tectonic -X compile`, a bare `.log` file's contents, or both
    concatenated (what `tectonic.compile_document` passes in). `default_file`
    names diagnostics that can't be attributed to a specific file by the
    matched pattern itself (the classic TeX log format and the warning
    formats don't carry a filename) -- pass the main .tex file's name for
    that case; left as `None` the diagnostic simply has no file.
    """
    lines = raw_log.splitlines()

    errors = _parse_terse_errors(lines)
    if not errors:
        errors = _parse_classic_errors(lines, default_file)

    warnings = _parse_warnings(lines, default_file)

    return errors + warnings
