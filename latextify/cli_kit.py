"""Offline install kit command (``make-kit``), split out of latextify.cli.

``make_kit_cmd`` is a plain function here; ``latextify.cli`` registers it on
the shared Typer ``app`` via ``app.command(name="make-kit")(make_kit_cmd)``
so this module stays free of the app object (and the cli module stays under
its size ceiling).
"""

from __future__ import annotations

from pathlib import Path

import typer


def make_kit_cmd(
    target: str = typer.Option(
        "current",
        "--target",
        help="Platform to build for: current, win-x64, linux-x64, or macos-arm64.",
    ),
    python_versions: list[str] = typer.Option(
        None,
        "--python-versions",
        help="CPython versions to cover, e.g. --python-versions 3.11 --python-versions 3.13 "
        "(default: 3.10 3.11 3.12 3.13 3.14).",
    ),
    output: Path = typer.Option(
        Path("build"), "--output", "-o", help="Directory to write the kit folder into."
    ),
    warm_tex: bool = typer.Option(
        True,
        "--warm-tex/--no-warm-tex",
        help="Pre-warm a TeX package cache so --pdf compiles offline (--no-warm-tex "
        "makes a smaller emit-only kit).",
    ),
    journals: str = typer.Option(
        None,
        "--journals",
        help="Comma-separated journals to warm (default: all registered). Ignored with "
        "--no-warm-tex.",
    ),
    with_gui: bool = typer.Option(
        False, "--with-gui", help="Also bundle the optional web-GUI dependency wheels."
    ),
    zip_kit: bool = typer.Option(
        False, "--zip", help="Also produce a .zip of the kit folder for distribution."
    ),
) -> None:
    """Build a self-contained offline install kit for an air-gapped machine.

    Packs the LaTeXtify wheel, every dependency wheel (per covered Python
    version), a Tectonic binary, and a pre-warmed TeX cache into
    ``<output>/latextify-offline-<os>-<arch>/``. The target machine runs its
    ``install.py`` with only a bare Python -- no internet, compiler, or admin
    rights. See ``latextify/kit/README-OFFLINE.md``.
    """
    from latextify.kit.build import DEFAULT_PY_VERSIONS, KitBuildError, make_kit

    py_versions = tuple(python_versions) if python_versions else DEFAULT_PY_VERSIONS
    journal_list = [j.strip() for j in journals.split(",") if j.strip()] if journals else None
    try:
        make_kit(
            target,
            python_versions=py_versions,
            output_dir=output,
            warm_tex=warm_tex,
            journals=journal_list,
            with_gui=with_gui,
            make_zip=zip_kit,
        )
    except KitBuildError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
