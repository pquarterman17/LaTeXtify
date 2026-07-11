"""Log-parser tests -- run unconditionally against captured log text.

No Tectonic binary or network access required: every sample here is a
verbatim excerpt captured from real `tectonic -X compile` runs (see plan
item 6), pasted as plain text.
"""

from latextify.compile.logs import parse_log
from latextify.model.compile import DiagnosticSeverity

# --- Tectonic's terse stderr summary line -----------------------------------


def test_terse_error_extracts_file_line_message():
    raw = (
        'note: "version 2" Tectonic command-line interface activated\n'
        "note: Running TeX ...\n"
        "Fontconfig error: Cannot load default config file: No such file: (null)\n"
        "error: main.tex:3: Undefined control sequence\n"
        "error: halted on potentially-recoverable error as specified\n"
    )
    diagnostics = parse_log(raw)
    errors = [d for d in diagnostics if d.severity is DiagnosticSeverity.ERROR]
    assert len(errors) == 1
    assert errors[0].file == "main.tex"
    assert errors[0].line == 3
    assert "Undefined control sequence" in errors[0].message


def test_terse_error_strips_doubled_bang_for_missing_class():
    raw = "error: main.tex:2: ! LaTeX Error: File `totallyFakeJournalClass.cls' not found.\n"
    diagnostics = parse_log(raw)
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity is DiagnosticSeverity.ERROR
    assert d.file == "main.tex"
    assert d.line == 2
    assert not d.message.startswith("!")
    assert "totallyFakeJournalClass.cls" in d.message
    assert "not found" in d.message


def test_terse_halted_summary_line_produces_no_extra_diagnostic():
    raw = (
        "error: main.tex:3: Undefined control sequence\n"
        "error: halted on potentially-recoverable error as specified\n"
    )
    diagnostics = parse_log(raw)
    assert len(diagnostics) == 1


# --- Classic "! message" / "l.N" block (planted \undefinedcommand) ----------


def test_classic_block_extracts_line_and_message_from_planted_undefined_command():
    # Verbatim shape of main.log for: Hello, world! \undefinedcommand{oops}
    raw = (
        "LaTeX Font Info:    ... okay on input line 2.\n"
        "\n"
        "! Undefined control sequence.\n"
        "l.3 Hello, world! \\undefinedcommand\n"
        "                                   {oops}\n"
        "No pages of output.\n"
    )
    diagnostics = parse_log(raw, default_file="main.tex")
    errors = [d for d in diagnostics if d.severity is DiagnosticSeverity.ERROR]
    assert len(errors) == 1
    assert errors[0].file == "main.tex"
    assert errors[0].line == 3
    assert errors[0].message == "Undefined control sequence."


def test_classic_block_with_intervening_help_lines_before_line_marker():
    # Verbatim shape of main.log for a missing \documentclass file.
    raw = (
        "L3 programming layer <2022-02-24>\n"
        "! ! LaTeX Error: File `totallyFakeJournalClass.cls' not found..\n"
        "\\@missingfileerror ...or: File `#1.#2' not found.}\n"
        "                                                  \n"
        "l.2 \\begin\n"
        "          {document}\n"
        "No pages of output.\n"
    )
    diagnostics = parse_log(raw, default_file="main.tex")
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.file == "main.tex"
    assert d.line == 2
    assert not d.message.startswith("!")
    assert "not found" in d.message


def test_classic_block_used_only_as_fallback_no_double_report():
    # Terse line AND classic block both present (real compile_document() shape):
    # terse must win, classic must not add a second diagnostic for the same error.
    raw = (
        "error: main.tex:3: Undefined control sequence\n"
        "error: halted on potentially-recoverable error as specified\n"
        "! Undefined control sequence.\n"
        "l.3 Hello, world! \\undefinedcommand\n"
        "                                   {oops}\n"
    )
    diagnostics = parse_log(raw, default_file="main.tex")
    errors = [d for d in diagnostics if d.severity is DiagnosticSeverity.ERROR]
    assert len(errors) == 1
    assert errors[0].file == "main.tex"  # came from the terse line, not default_file fallback


def test_classic_block_without_line_marker_still_reports_error_with_no_line():
    raw = "! Something went wrong.\nNo pages of output.\n"
    diagnostics = parse_log(raw)
    assert len(diagnostics) == 1
    assert diagnostics[0].line is None


# --- Warnings -----------------------------------------------------------------


def test_latex_warning_extracts_line_and_message():
    raw = "\nLaTeX Warning: Reference `fig:missing' on page 1 undefined on input line 3.\n"
    diagnostics = parse_log(raw, default_file="main.tex")
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity is DiagnosticSeverity.WARNING
    assert d.file == "main.tex"
    assert d.line == 3
    assert "undefined" in d.message


def test_latex_warning_without_line_number_still_reported():
    raw = "LaTeX Warning: There were undefined references.\n"
    diagnostics = parse_log(raw)
    assert len(diagnostics) == 1
    assert diagnostics[0].line is None
    assert diagnostics[0].severity is DiagnosticSeverity.WARNING


def test_package_warning_includes_package_name():
    raw = "Package hyperref Warning: Token not allowed in a PDF string on input line 42.\n"
    diagnostics = parse_log(raw, default_file="main.tex")
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity is DiagnosticSeverity.WARNING
    assert d.line == 42
    assert "hyperref" in d.message


def test_bibtex_warning_extracted_without_file_or_line():
    raw = "Warning--jnrlst (dependency: not reversed) set 1\n"
    diagnostics = parse_log(raw, default_file="main.tex")
    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity is DiagnosticSeverity.WARNING
    assert d.file is None
    assert d.line is None
    assert "jnrlst" in d.message


# --- Noise suppression: never show raw TeX spew --------------------------------


def test_clean_compile_log_produces_no_diagnostics():
    # Verbatim (trimmed) shape of a successful plain-article compile.
    raw = (
        'note: "version 2" Tectonic command-line interface activated\n'
        "note: generating format \"latex\"\n"
        "note: downloading article.cls\n"
        "note: downloading size10.clo\n"
        "Fontconfig error: Cannot load default config file: No such file: (null)\n"
        "note: Running TeX ...\n"
        "LaTeX Font Info:    Checking defaults for OML/cmm/m/it on input line 2.\n"
        "LaTeX Font Info:    ... okay on input line 2.\n"
        "\\c@part=\\count181\n"
        "note: Writing `main.pdf` (2.9873046875 KiB)\n"
        "note: Writing `main.log` (2.0341796875 KiB)\n"
    )
    assert parse_log(raw) == []


def test_font_and_progress_chatter_ignored_alongside_real_error():
    raw = (
        "note: downloading article.cls\n"
        "\\c@part=\\count181\n"
        "LaTeX Font Info:    ... okay on input line 2.\n"
        "error: main.tex:3: Undefined control sequence\n"
        "error: halted on potentially-recoverable error as specified\n"
    )
    diagnostics = parse_log(raw)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "Undefined control sequence"
