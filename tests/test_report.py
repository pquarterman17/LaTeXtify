"""Tests for latextify.report.render (plan item 16)."""

from __future__ import annotations

from pathlib import Path

from latextify.model.compile import CompileDiagnostic, CompileResult, DiagnosticSeverity
from latextify.model.emit import EmitResult, EmitWarning
from latextify.model.figure import Figure, FigureSource
from latextify.model.preflight import (
    Location,
    PreflightFinding,
    PreflightReport,
    Severity,
    StyleInventory,
)
from latextify.model.reconcile import ReconcileRecord, ReconciliationReport
from latextify.report.render import render_report


def _emit_result(**overrides):
    """An EmitResult with sensible defaults; override any field per test."""
    defaults = dict(
        output_dir=Path("/tmp"),
        journal_name="test",
        main_tex_path=Path("/tmp/main.tex"),
        main_tex_written=True,
        preamble_tex_path=Path("/tmp/gen/preamble.tex"),
        metadata_tex_path=Path("/tmp/gen/metadata.tex"),
        body_tex_path=Path("/tmp/gen/body.tex"),
        bib_path=Path("/tmp/references.bib"),
        figures_dir=Path("/tmp/figures"),
        figure_count=0,
        citation_count=0,
    )
    defaults.update(overrides)
    return EmitResult(**defaults)


class TestRenderReportEmptySections:
    """Empty sections should display '_None_' for stable diffs."""

    def test_all_empty(self):
        styles = StyleInventory(frozenset(), False, False)
        report_text = render_report(
            preflight=PreflightReport(findings=(), styles=styles),
            emit_result=EmitResult(
                output_dir=Path("/tmp"),
                journal_name="test",
                main_tex_path=Path("/tmp/main.tex"),
                main_tex_written=True,
                preamble_tex_path=Path("/tmp/gen/preamble.tex"),
                metadata_tex_path=Path("/tmp/gen/metadata.tex"),
                body_tex_path=Path("/tmp/gen/body.tex"),
                bib_path=Path("/tmp/references.bib"),
                figures_dir=Path("/tmp/figures"),
                figure_count=0,
                citation_count=0,
            ),
        )

        assert "## Preflight Findings\n_None_" in report_text
        assert "## Citation Extraction\n_None_" in report_text
        assert "## Figures\n_None_" in report_text
        assert "## Compilation\n_Not compiled_" in report_text

    def test_preflight_none_when_empty(self):
        styles = StyleInventory(frozenset(), False, False)
        report_text = render_report(
            preflight=PreflightReport(findings=(), styles=styles),
        )
        assert "## Preflight Findings\n_None_" in report_text


class TestPreflightOrdering:
    """Preflight findings should sort by severity then location."""

    def test_sorted_by_severity_then_location(self):
        findings = (
            PreflightFinding(
                severity=Severity.WARN,
                detector="test",
                location=Location(paragraph_index=10, text_snippet="warn10"),
                message="warning at para 10",
            ),
            PreflightFinding(
                severity=Severity.ERROR,
                detector="test",
                location=Location(paragraph_index=5, text_snippet="error5"),
                message="error at para 5",
            ),
            PreflightFinding(
                severity=Severity.INFO,
                detector="test",
                location=Location(paragraph_index=1, text_snippet="info1"),
                message="info at para 1",
            ),
            PreflightFinding(
                severity=Severity.ERROR,
                detector="test",
                location=Location(paragraph_index=2, text_snippet="error2"),
                message="error at para 2",
            ),
        )
        styles = StyleInventory(frozenset(), False, False)
        preflight = PreflightReport(findings=findings, styles=styles)
        report_text = render_report(preflight=preflight)

        # Should be: error at para 2, error at para 5, warn at para 10, info at para 1
        assert "error at para 2" in report_text
        idx_error2 = report_text.index("error at para 2")
        idx_error5 = report_text.index("error at para 5")
        idx_warn10 = report_text.index("warning at para 10")
        idx_info1 = report_text.index("info at para 1")

        # Error severity first
        assert idx_error2 < idx_error5 < idx_warn10 < idx_info1


class TestCitationExtractionReporting:
    """Citation extraction section reports on reconciliation or field codes."""

    def test_plaintext_reconciliation_with_verify_flag(self):
        records = (
            ReconcileRecord(
                raw_text="Smith et al., 2020",
                key="smith2020",
                source="crossref",
                matched=True,
                score=0.95,
                doi="10.1234/test",
                verify=False,
                ref_number=1,
                matched_title="A Test Paper",
            ),
            ReconcileRecord(
                raw_text="Jones, 2019",
                key="jones2019",
                source="crossref",
                matched=False,
                score=0.50,
                doi=None,
                verify=True,
                ref_number=2,
                matched_title=None,
            ),
        )
        reconciliation = ReconciliationReport(records=records)
        report_text = render_report(reconciliation=reconciliation)

        # Should include score and verify flag
        assert "smith2020" in report_text
        assert "jones2019" in report_text
        assert "⚠️ VERIFY" in report_text
        assert "score=0.95" in report_text
        assert "50%" in report_text  # 1/2 matched

    def test_field_codes_path_simple_count(self):
        emit = EmitResult(
            output_dir=Path("/tmp"),
            journal_name="test",
            main_tex_path=Path("/tmp/main.tex"),
            main_tex_written=True,
            preamble_tex_path=Path("/tmp/gen/preamble.tex"),
            metadata_tex_path=Path("/tmp/gen/metadata.tex"),
            body_tex_path=Path("/tmp/gen/body.tex"),
            bib_path=Path("/tmp/references.bib"),
            figures_dir=Path("/tmp/figures"),
            figure_count=0,
            citation_count=5,
        )
        report_text = render_report(emit_result=emit)
        assert "Extracted 5 citations from field codes" in report_text


class TestFigureReporting:
    """Figures should sort by number and show source + conversion notes."""

    def test_figures_sorted_by_number(self):
        figures = (
            Figure(
                number=3,
                caption="Third figure",
                embedded_path=Path("media/image3.png"),
                source=FigureSource.EMBEDDED,
            ),
            Figure(
                number=1,
                caption="First figure",
                embedded_path=Path("media/image1.pdf"),
                override_path=Path("figures/fig1.pdf"),
                source=FigureSource.MANIFEST,
            ),
            Figure(
                number=2,
                caption="Second figure",
                embedded_path=Path("media/image2.svg"),
                override_path=Path("figures/fig2.pdf"),
                source=FigureSource.OVERRIDE,
                conversion_note="SVG → PDF (cairosvg)",
            ),
        )
        emit = EmitResult(
            output_dir=Path("/tmp"),
            journal_name="test",
            main_tex_path=Path("/tmp/main.tex"),
            main_tex_written=True,
            preamble_tex_path=Path("/tmp/gen/preamble.tex"),
            metadata_tex_path=Path("/tmp/gen/metadata.tex"),
            body_tex_path=Path("/tmp/gen/body.tex"),
            bib_path=Path("/tmp/references.bib"),
            figures_dir=Path("/tmp/figures"),
            figure_count=3,
            citation_count=0,
            figures=figures,
        )
        report_text = render_report(emit_result=emit)

        # Figures should appear in order 1, 2, 3
        idx_fig1 = report_text.index("Fig 1")
        idx_fig2 = report_text.index("Fig 2")
        idx_fig3 = report_text.index("Fig 3")
        assert idx_fig1 < idx_fig2 < idx_fig3

        # Check sources
        assert "MANIFEST" in report_text
        assert "OVERRIDE" in report_text
        assert "EMBEDDED" in report_text

        # Check conversion note
        assert "SVG → PDF" in report_text


class TestCompilationReporting:
    """Compilation diagnostics should sort by severity then location."""

    def test_compile_success(self):
        compile_result = CompileResult(
            success=True,
            pdf_path=Path("/tmp/output.pdf"),
            diagnostics=(),
            raw_log="",
            returncode=0,
        )
        report_text = render_report(compile_result=compile_result)
        assert "✓ **Success**" in report_text

    def test_compile_failure_with_diagnostics(self):
        diags = (
            CompileDiagnostic(
                severity=DiagnosticSeverity.WARNING,
                message="Overfull hbox",
                file="body.tex",
                line=42,
            ),
            CompileDiagnostic(
                severity=DiagnosticSeverity.ERROR,
                message="Undefined control sequence",
                file="preamble.tex",
                line=10,
            ),
            CompileDiagnostic(
                severity=DiagnosticSeverity.ERROR,
                message="Package error",
                file=None,
                line=None,
            ),
        )
        compile_result = CompileResult(
            success=False,
            pdf_path=None,
            diagnostics=diags,
            raw_log="",
            returncode=1,
        )
        report_text = render_report(compile_result=compile_result)

        assert "✗ **Failed**" in report_text
        # Errors should come before warnings
        assert report_text.index("[ERROR]") < report_text.index("[WARNING]")
        # Check that diagnostics are present
        assert "Undefined control sequence" in report_text
        assert "preamble.tex:10" in report_text


class TestWarningsReporting:
    """Emit-stage warnings must reach the consolidated report (previously dropped)."""

    def test_warnings_rendered_in_report(self):
        emit = _emit_result(
            warnings=(
                EmitWarning(message="figure 3: Ghostscript not found; install it."),
                EmitWarning(message="unresolved figure anchor for figure 99"),
            ),
        )
        report_text = render_report(emit_result=emit)
        assert "## Warnings" in report_text
        assert "Ghostscript not found" in report_text
        assert "unresolved figure anchor for figure 99" in report_text

    def test_warnings_none_when_empty(self):
        report_text = render_report(emit_result=_emit_result(warnings=()))
        assert "## Warnings\n_None_" in report_text

    def test_warnings_none_when_no_emit_result(self):
        report_text = render_report()
        assert "## Warnings\n_None_" in report_text

    def test_warnings_sorted_for_stable_diffs(self):
        emit = _emit_result(
            warnings=(
                EmitWarning(message="zeta warning"),
                EmitWarning(message="alpha warning"),
            ),
        )
        report_text = render_report(emit_result=emit)
        assert report_text.index("alpha warning") < report_text.index("zeta warning")

    def test_newline_in_warning_does_not_break_list_structure(self):
        # Markdown-injection guard: a newline-laden message stays one bullet.
        emit = _emit_result(
            warnings=(EmitWarning(message="line one\n- fake bullet\nline three"),),
        )
        report_text = render_report(emit_result=emit)
        warnings_section = report_text.split("## Warnings", 1)[1]
        # Exactly one list item in the Warnings section (no injected bullet).
        assert warnings_section.count("\n- ") == 1
        assert "line one - fake bullet line three" in report_text

    def test_very_long_message_does_not_crash(self):
        emit = _emit_result(warnings=(EmitWarning(message="x" * 20000),))
        report_text = render_report(emit_result=emit)
        assert "x" * 20000 in report_text


class TestInjectionHardening:
    """Newlines in preflight/compile/figure text must not mangle the report."""

    def test_newline_in_preflight_message_flattened(self):
        findings = (
            PreflightFinding(
                severity=Severity.ERROR,
                detector="d",
                location=Location(paragraph_index=1, text_snippet=""),
                message="bad thing\n## Injected Heading\nmore",
            ),
        )
        styles = StyleInventory(frozenset(), False, False)
        report_text = render_report(
            preflight=PreflightReport(findings=findings, styles=styles)
        )
        # The injected heading must not become a real report heading.
        assert "\n## Injected Heading" not in report_text
        assert "bad thing ## Injected Heading more" in report_text

    def test_newline_in_figure_caption_flattened(self):
        figures = (
            Figure(
                number=1,
                caption="cap line 1\ncap line 2",
                embedded_path=Path("media/image1.png"),
                source=FigureSource.EMBEDDED,
            ),
        )
        report_text = render_report(emit_result=_emit_result(figure_count=1, figures=figures))
        assert "cap line 1 cap line 2" in report_text


class TestZeroOfEverything:
    """A run with nothing to report must still produce a full, stable report."""

    def test_all_none_arguments(self):
        report_text = render_report()
        for section in (
            "## Preflight Findings",
            "## Citation Extraction",
            "## Figures",
            "## Compilation",
            "## Warnings",
        ):
            assert section in report_text


class TestReportStability:
    """Re-rendering the same data should produce identical output."""

    def test_stable_across_runs(self):
        findings = (
            PreflightFinding(
                severity=Severity.ERROR,
                detector="detector1",
                location=Location(5, "snippet"),
                message="msg1",
            ),
        )
        styles = StyleInventory(frozenset(), False, False)
        preflight = PreflightReport(findings=findings, styles=styles)

        render1 = render_report(preflight=preflight)
        render2 = render_report(preflight=preflight)

        assert render1 == render2
