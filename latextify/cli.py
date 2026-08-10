"""Command-line interface.

Current surface (plan items 3, 5, 16, 18, 19, 20, 21, 23):

    latextify convert paper.docx --journal revtex4-2 [--output output] \\
        [--citation-style numeric|authoryear] [--pdf] [--report/--no-report] \\
        [--exclude-figures] \\  # text-only project (no figures)
        [--keep-figure-metadata] \\  # don't strip figure EXIF/GPS (default: strip)
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

import webbrowser
from pathlib import Path

import typer

from latextify.cli_batch import batch
from latextify.cli_convert import convert
from latextify.cli_equations import equations
from latextify.cli_export import export
from latextify.cli_kit import make_kit_cmd
from latextify.cli_privacy import clean, formats
from latextify.cli_privacy import inspect as inspect_cmd
from latextify.templates import loader
from latextify.templates.loader import ManifestError

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _callback() -> None:
    """LaTeXtify: convert Word manuscripts into journal-ready LaTeX projects."""


# The two big commands live in their own modules to keep this one a registry:
# `convert` carries every submission option the tool has (cli_convert), and
# batch conversion (item 20) is cli_batch. Both register on the shared app.
app.command()(convert)
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

# Metadata inspection + sanitizing (METADATA_PRIVACY_PLAN) live in
# latextify.cli_privacy. `clean` supersedes the docx-only command: it routes
# through latextify.privacy.registry, so Word, PowerPoint, Excel, PDF and
# images are all reachable from the one command.
app.command(name="clean")(clean)
app.command(name="inspect")(inspect_cmd)
app.command(name="formats")(formats)

# HTML/Markdown export (items 4-5, FORMATS_AND_PRIVACY) lives in
# latextify.cli_export; register its command on the shared app.
app.command(name="export")(export)


@app.command()
def gui(
    port: int = typer.Option(8501, "--port", help="Port to bind the local GUI server to."),
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
