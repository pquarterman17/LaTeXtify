"""Command-line interface.

Current surface (plan items 3, 5, 16, 18, 19, 20, 21, 23):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear] [--pdf] [--report/--no-report] \\
        [--exclude-figures] \\  # text-only project (no figures)
        [--columns default|one|two] [--line-numbers] [--double-spacing] \\
        [--anonymize] [--figures-at-end] \\  # submission/layout options
        [--supplement si.docx] [--combine-supplement] \\  # Supplementary Material (item 21)
        [--supplement-columns default|one|two] [--supplement-line-numbers] \\
        [--supplement-double-spacing] \\
        [--check-references] [--review]  # online Crossref check + interactive review
    latextify batch folder --journal J [--citation-style S] [--pdf] \\
        [--output output] [--recursive]          # batch conversion (item 20)
    latextify journals              # list registered journal templates (item 18)
    latextify equations paper.docx [--output DIR] [--pdf]  # equation audit (item 23)
    latextify clean paper.docx clean.docx  # strip metadata/tracked changes/comments (item 3)
    latextify export paper.docx --format html|markdown [--output FILE] \\
        [--crossref-mailto EMAIL] [--references FILE]  # HTML/Markdown export (items 4-5)
    latextify gui [--port 8501] [--no-browser] [--workdir DIR] \\
        [--keep-alive]  # local web GUI (item 19)

Planned (later items):
    latextify preflight paper.docx  # validation report only, no conversion
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import typer

from latextify.citations.bib import entries_to_bib
from latextify.citations.corrections import apply_corrections
from latextify.cli_batch import batch
from latextify.cli_clean import clean
from latextify.cli_equations import equations
from latextify.cli_export import export
from latextify.cli_kit import make_kit_cmd
from latextify.cli_review import review_corrections
from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.emit.submission import parse_layout_form
from latextify.model.emit import EmitResult
from latextify.report.render import write_report
from latextify.templates import loader
from latextify.templates.loader import ManifestError, load

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _callback() -> None:
    """LaTeXtify: convert Word manuscripts into journal-ready LaTeX projects."""


@app.command()
def convert(
    docx_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Source .docx manuscript to convert."
    ),
    journal: str = typer.Option(
        ..., "--journal", "-j", help="Target journal template (e.g. 'revtex4-2')."
    ),
    output: Path = typer.Option(
        Path("output"), "--output", "-o", help="Output root directory."
    ),
    citation_style: str = typer.Option(
        None,
        "--citation-style",
        help="Citation mode override: numeric|authoryear (journal-dependent).",
    ),
    crossref_mailto: str = typer.Option(
        None,
        "--crossref-mailto",
        help="Contact email sent to Crossref when reconstructing plain-text "
        "citations (documents with no citation field codes). Recommended.",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Compile LaTeX to PDF after emission using Tectonic.",
    ),
    report: bool = typer.Option(
        True,
        "--report/--no-report",
        help="Generate report.md (default: on).",
    ),
    exclude_figures: bool = typer.Option(
        False,
        "--exclude-figures",
        help="Emit a text-only project: drop every figure (no \\includegraphics, "
        "no captions) and copy no images. Tables, equations, and citations are "
        "kept. Applies to the supplement too. Off by default.",
    ),
    columns: str = typer.Option(
        "default",
        "--columns",
        help="Main document column mode: default|one|two.",
    ),
    line_numbers: bool = typer.Option(
        False,
        "--line-numbers/--no-line-numbers",
        help="Reviewer line numbers on the main document.",
    ),
    double_spacing: bool = typer.Option(
        False,
        "--double-spacing/--no-double-spacing",
        help="Double-space the main document body.",
    ),
    anonymize: bool = typer.Option(
        False,
        "--anonymize/--no-anonymize",
        help="Double-blind submission: placeholder author, no affiliations, "
        "acknowledgments stripped from the main document.",
    ),
    figures_at_end: bool = typer.Option(
        False,
        "--figures-at-end/--no-figures-at-end",
        help="Gather figures/tables after the references (endfloat). Applies "
        "to the supplement too.",
    ),
    supplement: Path = typer.Option(
        None,
        "--supplement",
        exists=True,
        readable=True,
        help="Second .docx to emit as Supplementary Material: writes "
        "supplement.tex (S-numbered figures/tables/equations/sections) "
        "sharing this project's figures/ and references.bib with the main "
        "document.",
    ),
    combine_supplement: bool = typer.Option(
        False,
        "--combine-supplement",
        help="Staple the main document and the supplement into one combined.pdf "
        "(requires --supplement and --pdf). The separate main.pdf/supplement.pdf "
        "are still written.",
    ),
    references: Path = typer.Option(
        None,
        "--references",
        exists=True,
        readable=True,
        help="A .bib/.ris/CSL-JSON/EndNote-XML/.nbib export of your reference "
        "manager. On documents with no citation field codes, each typed reference "
        "is matched against it first (authoritative, offline); only references it "
        "doesn't cover fall back to Crossref. Shared with the supplement.",
    ),
    supplement_onecolumn: bool = typer.Option(
        False,
        "--supplement-onecolumn",
        help="Emit the supplement as a simplified one-column article "
        "(\\documentclass[11pt]{article}) instead of the journal class, keeping "
        "S-numbering and the shared references/figures. Needs --supplement.",
    ),
    supplement_columns: str = typer.Option(
        "default",
        "--supplement-columns",
        help="Supplement column mode: default|one|two. Needs --supplement.",
    ),
    supplement_line_numbers: bool = typer.Option(
        False,
        "--supplement-line-numbers/--no-supplement-line-numbers",
        help="Reviewer line numbers on the supplement. Needs --supplement.",
    ),
    supplement_double_spacing: bool = typer.Option(
        False,
        "--supplement-double-spacing/--no-supplement-double-spacing",
        help="Double-space the supplement. Needs --supplement.",
    ),
    check_references: bool = typer.Option(
        False,
        "--check-references",
        help="Validate every reference online against Crossref (needs internet): "
        "resolve each DOI and compare title/authors/year/journal/volume/pages, "
        "and suggest DOIs for references that lack one. Results go to report.md. "
        "Off by default.",
    ),
    review: bool = typer.Option(
        False,
        "--review",
        help="Interactively review the online reference check: step through each "
        "flagged reference and approve / deny / edit the correction, then rewrite "
        "references.bib and recompile. Implies --check-references. Needs a "
        "terminal (skipped when stdin is not a TTY).",
    ),
) -> None:
    """Convert DOCX_PATH into a journal-ready LaTeX project under output/<journal>/."""
    if combine_supplement and supplement is None:
        typer.echo("error: --combine-supplement requires --supplement", err=True)
        raise typer.Exit(code=1)
    if combine_supplement and not pdf:
        typer.echo("error: --combine-supplement requires --pdf", err=True)
        raise typer.Exit(code=1)
    if supplement_onecolumn and supplement is None:
        typer.echo("error: --supplement-onecolumn requires --supplement", err=True)
        raise typer.Exit(code=1)
    # --review turns on the online check it reviews.
    check_references = check_references or review
    # Per-document layout overrides (mirrors the GUI's convert-multi wiring in
    # latextify/gui/server.py); a bad --columns value is a clean error naming
    # the field, before anything touches disk.
    try:
        main_layout = parse_layout_form(columns, line_numbers, double_spacing)
        supplement_layout = parse_layout_form(
            supplement_columns, supplement_line_numbers, supplement_double_spacing
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        journal_obj = load(journal)
        result = emit_project(
            docx_path,
            journal,
            output,
            citation_style=citation_style,
            crossref_mailto=crossref_mailto,
            report=report,
            exclude_figures=exclude_figures,
            main_layout=main_layout,
            supplement_layout=supplement_layout,
            anonymize=anonymize,
            figures_at_end=figures_at_end,
            supplement_docx_path=supplement,
            references_bib_path=references,
            supplement_onecolumn=supplement_onecolumn,
            check_references=check_references,
        )
    except ManifestError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        # Every ingest-boundary module (preflight, metadata_guess, pandoc)
        # raises a clean ValueError naming the problem for a corrupt or
        # unsupported .docx -- never let it surface as a raw, unhandled
        # traceback here (ManifestError is itself a ValueError subclass, so
        # this branch also covers it defensively).
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {result.output_dir}")
    if not result.main_tex_written:
        typer.echo("main.tex already existed -- left untouched (edit it directly)")
    if result.supplement is not None and not result.supplement.supplement_tex_written:
        typer.echo("supplement.tex already existed -- left untouched (edit it directly)")
    for warning in result.warnings:
        typer.echo(f"warning: {warning.message}")
    if result.supplement is not None:
        for warning in result.supplement.warnings:
            typer.echo(f"warning: {warning.message}")

    # Interactive reference review (--review): step through the online check's
    # flagged references, apply the author's accepted corrections to
    # references.bib, and let the compile step below build the corrected PDF.
    # Runs BEFORE compilation so there is exactly one (correct) compile.
    if review:
        _run_interactive_review(result)

    # Compile step (item 16: CLI wiring for --pdf flag; item 21 compiles the
    # supplement too when one was emitted).
    compile_result = None
    supplement_compile_result = None
    if pdf:
        try:
            vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
            compile_result = compile_document(
                result.main_tex_path,
                tectonic_path=ensure_tectonic(),
                vendor_dir=vendor_dir,
            )
            if compile_result.success:
                typer.echo(f"compiled {compile_result.pdf_path}")
            else:
                typer.echo("compilation failed (see report.md)", err=True)

            if result.supplement is not None:
                supplement_compile_result = compile_document(
                    result.supplement.supplement_tex_path,
                    tectonic_path=ensure_tectonic(),
                    vendor_dir=vendor_dir,
                )
                if supplement_compile_result.success:
                    typer.echo(f"compiled {supplement_compile_result.pdf_path}")
                else:
                    typer.echo("supplement compilation failed (see report.md)", err=True)

            # Staple main + supplement into one combined.pdf when asked and both
            # compiled (validated above to require --supplement and --pdf).
            if (
                combine_supplement
                and compile_result.success
                and supplement_compile_result is not None
                and supplement_compile_result.success
            ):
                from latextify.compile.pdf import staple_pdfs

                combined = result.output_dir / "combined.pdf"
                staple_pdfs(
                    [compile_result.pdf_path, supplement_compile_result.pdf_path], combined
                )
                typer.echo(f"combined {combined}")
        except Exception as exc:
            typer.echo(f"error: compilation failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    # Update report with compile diagnostics if compilation ran.
    if report and compile_result is not None:
        write_report(
            result.report_path or result.output_dir / "report.md",
            preflight=None,  # Already included in initial report
            emit_result=result,
            reconciliation=None,  # Already included
            compile_result=compile_result,
            supplement=result.supplement,
            supplement_compile=supplement_compile_result,  # surface SI compile diagnostics
            validation=result.validation,  # keep the section on the post-compile rewrite
        )

    # Exit code policy (item 16): nonzero if compile errors (item 21: either document).
    exit_code = 0
    if compile_result is not None and not compile_result.success:
        exit_code = 1
    if supplement_compile_result is not None and not supplement_compile_result.success:
        exit_code = 1

    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def _run_interactive_review(result: EmitResult) -> None:
    """Drive the console reference review and apply accepted fixes to the .bib.

    No-ops (with a short note) when the online check found nothing to review or
    when stdin is not a terminal -- the review needs interactive input, so a
    piped/CI invocation of ``--review`` degrades to "check ran, report written"
    rather than blocking on a prompt that can never be answered.
    """
    validation = result.validation
    if validation is None or validation.flagged_count == 0:
        if validation is not None:
            typer.echo("reference check: nothing flagged -- no review needed.")
        return
    if not sys.stdin.isatty():
        typer.echo(
            f"warning: --review needs a terminal; {validation.flagged_count} flagged "
            "reference(s) left unchanged (see report.md).",
            err=True,
        )
        return

    decisions = review_corrections(
        list(result.entries), validation, prompt=input, echo=typer.echo
    )
    applied = [d for d in decisions if d.action in ("approve", "edit")]
    if not applied:
        typer.echo("no corrections applied.")
        return

    corrected = apply_corrections(list(result.entries), validation, decisions)
    result.bib_path.write_text(entries_to_bib(corrected), encoding="utf-8")
    typer.echo(
        f"applied {len(applied)} correction(s) to {result.bib_path.name} "
        "(recompiling with the fixes if --pdf was given)."
    )


# Batch conversion (item 20) lives in latextify.cli_batch to keep this
# module focused; register its command on the shared app.
app.command()(batch)
@app.command()
def journals() -> None:
    """List registered journal templates with their available citation modes."""
    discovered = loader.discover()
    if not discovered:
        typer.echo("No journals registered.")
        return

    for journal_name in sorted(discovered.keys()):
        try:
            journal = loader.load(journal_name)
            modes = sorted(journal.bib_modes.keys())
            modes_str = ", ".join(modes)
            typer.echo(f"{journal_name}: {modes_str}")
        except ManifestError as exc:
            typer.echo(f"{journal_name}: error loading manifest: {exc}", err=True)


# Offline install kit (make-kit) lives in latextify.cli_kit to keep this
# module focused; register its command on the shared app.
app.command(name="make-kit")(make_kit_cmd)


# Equation audit (item 23) lives in latextify.cli_equations to keep this
# module focused; register its command on the shared app.
app.command()(equations)

# Docx sanitizer (item 3, FORMATS_AND_PRIVACY) lives in latextify.cli_clean;
# register its command on the shared app.
app.command(name="clean")(clean)

# HTML/Markdown export (items 4-5, FORMATS_AND_PRIVACY) lives in
# latextify.cli_export; register its command on the shared app.
app.command(name="export")(export)


@app.command()
def gui(
    port: int = typer.Option(
        8501, "--port", help="Port to bind the local GUI server to."
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't automatically open a browser window.",
    ),
    workdir: Path = typer.Option(
        None,
        "--workdir",
        help="Directory for per-conversion working files (default: a fresh temp dir).",
    ),
    keep_alive: bool = typer.Option(
        False,
        "--keep-alive",
        help="Don't auto-exit when the last browser tab showing the GUI closes "
        "(default: exits automatically, same as Ctrl+C).",
    ),
) -> None:
    """Start a local web GUI (drag-and-drop, journal picker, PDF preview).

    Binds 127.0.0.1 only -- this is a local tool and uploaded manuscripts
    are private, never exposed on the network. Requires the optional 'gui'
    extra (fastapi, uvicorn, python-multipart); see the error message below
    if it isn't installed. Exits on its own once the browser tab is closed
    (see latextify.gui.lifecycle); pass --keep-alive to require Ctrl+C instead.
    """
    try:
        import uvicorn

        from latextify.gui.server import create_app
    except ImportError as exc:
        typer.echo(
            "error: the GUI requires optional dependencies that aren't installed.\n"
            "Install them with:\n"
            "  uv pip install 'latextify[gui]'\n"
            "or:\n"
            "  pip install 'latextify[gui]'",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    application = create_app(workdir=workdir, auto_shutdown=not keep_alive)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"LaTeXtify GUI running at {url} (Ctrl+C to stop)")
    if not no_browser:
        webbrowser.open(url)
    # Built as an explicit Server (not uvicorn.run) so the app itself can
    # request a clean stop once every browser tab closes -- see
    # latextify.gui.lifecycle.start_client_monitor, wired up only when
    # auto_shutdown=True.
    config = uvicorn.Config(application, host="127.0.0.1", port=port)
    server = uvicorn.Server(config)
    application.state.shutdown = lambda: setattr(server, "should_exit", True)
    server.run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
