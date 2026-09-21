"""Entry point for the no-install Windows portable application.

This module is deliberately separate from the normal CLI.  A frozen GUI has
no terminal to display a traceback in, must select a free local port, and must
point at the Tectonic/cache directories shipped beside the executable before
any LaTeXtify modules are imported.
"""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import socket
import subprocess
import sys
import tempfile
import traceback
import webbrowser
from pathlib import Path
from typing import TextIO


def application_dir() -> Path:
    """Return the stable directory containing the portable application."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def configure_offline_environment(root: Path) -> None:
    """Use only the Tectonic binary and TeX cache bundled beside the app."""
    os.environ["LATEXTIFY_OFFLINE"] = "1"
    tectonic_dir = root / "tectonic"
    tex_cache = root / "tex-bundle-cache"
    if tectonic_dir.is_dir():
        os.environ["PATH"] = str(tectonic_dir) + os.pathsep + os.environ.get("PATH", "")
    if tex_cache.is_dir():
        os.environ["TECTONIC_CACHE_DIR"] = str(tex_cache)


def choose_port(preferred: int = 8501) -> int:
    """Choose the usual GUI port when available, otherwise an ephemeral one."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    raise OSError("no local TCP port is available for the LaTeXtify interface")


def _open_log(root: Path) -> tuple[Path, TextIO]:
    """Open a user-writable startup log, preferring the portable directory."""
    candidates = [root / "latextify-startup.log"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "LaTeXtify" / "latextify-startup.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path, path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
    raise OSError("could not create the LaTeXtify startup log")


def _error_dialog(message: str) -> None:
    """Show a native error without pulling an additional GUI toolkit into the build."""
    ctypes.windll.user32.MessageBoxW(None, message, "LaTeXtify could not start", 0x10)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def self_test(root: Path) -> None:
    """Exercise frozen resources and native tools without starting the server."""
    import pypandoc

    from latextify.compile.tectonic import compile_document, ensure_tectonic
    from latextify.gui.server import create_app
    from latextify.templates import loader

    frozen_data = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    static_index = frozen_data / "latextify" / "gui" / "static" / "index.html"
    if not static_index.is_file():
        raise RuntimeError("the packaged GUI files are missing")
    if not loader.available():
        raise RuntimeError("the packaged journal templates are missing")
    print(f"pandoc: {pypandoc.get_pandoc_version()}")
    converted = pypandoc.convert_text(
        "Portable Pandoc check", to="latex", format="markdown", verify_format=False
    )
    if "Portable Pandoc check" not in converted:
        raise RuntimeError("the packaged Pandoc conversion check failed")
    tectonic = ensure_tectonic()
    subprocess.run([str(tectonic), "--version"], check=True, capture_output=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix="latextify-portable-check-") as temp:
        tex = Path(temp) / "main.tex"
        tex.write_text(
            "\\documentclass{revtex4-2}\\begin{document}Portable PDF check\\end{document}\n",
            encoding="utf-8",
        )
        compiled = compile_document(tex, tectonic_path=tectonic, timeout=60)
        if not compiled.success:
            raise RuntimeError(f"the packaged offline PDF check failed:\n{compiled.raw_log}")
    create_app(auto_shutdown=False)
    sample = root / "sample" / "Combined-Manuscript-with-EMF.docx"
    if sample.is_file():
        from latextify.emit.project import emit_project
        from latextify.emit.submission import DocumentLayout

        with tempfile.TemporaryDirectory(prefix="latextify-combined-emf-check-") as temp:
            source_dir = Path(temp) / "input"
            source_dir.mkdir()
            sample_copy = source_dir / sample.name
            sample_copy.write_bytes(sample.read_bytes())
            sample_sidecar = sample.with_name("paper.yaml")
            if sample_sidecar.is_file():
                (source_dir / "paper.yaml").write_bytes(sample_sidecar.read_bytes())
            result = emit_project(
                sample_copy,
                "revtex4-2",
                Path(temp) / "output",
                inline_supplement=True,
                inline_supplement_columns="one",
                main_layout=DocumentLayout(columns="two"),
                figure_placements={("", 1): "one", ("", 2): "two"},
            )
            body = result.body_tex_path.read_text(encoding="utf-8")
            required = ("\\begin{figure}\n", "\\begin{figure*}\n", "\\clearpage", "\\onecolumn")
            if not all(marker in body for marker in required):
                raise RuntimeError("the combined Word/EMF layout check produced incorrect LaTeX")
            compiled = compile_document(result.main_tex_path, tectonic_path=tectonic, timeout=90)
            if not compiled.success:
                raise RuntimeError(f"the combined Word/EMF PDF check failed:\n{compiled.raw_log}")
    print("portable self-test passed")


def verify_installation(root: Path) -> None:
    from latextify.integrity import format_result, verify_manifest

    result = verify_manifest(root)
    print(format_result(result))
    if not result.ok:
        raise RuntimeError("installation integrity verification failed")


def run_gui() -> None:
    """Start the private browser GUI from the frozen application."""
    import uvicorn

    from latextify.gui.server import create_app

    requested_port = int(os.environ.get("LATEXTIFY_PORT", "8501"))
    port = choose_port(requested_port)
    url = f"http://127.0.0.1:{port}"
    application = create_app(auto_shutdown=True)
    config = uvicorn.Config(application, host="127.0.0.1", port=port)
    server = uvicorn.Server(config)
    application.state.shutdown = lambda: setattr(server, "should_exit", True)
    if not _enabled("LATEXTIFY_NO_BROWSER"):
        webbrowser.open(url)
    server.run()


def main() -> None:
    multiprocessing.freeze_support()
    root = application_dir()
    configure_offline_environment(root)
    try:
        log_path, log = _open_log(root)
    except OSError as exc:
        _error_dialog(str(exc))
        return

    with log:
        sys.stdout = log
        sys.stderr = log
        try:
            print("\n=== LaTeXtify portable startup ===")
            print(f"application directory: {root}")
            if "--verify-installation" in sys.argv:
                verify_installation(root)
            elif "--self-test" in sys.argv:
                self_test(root)
            else:
                run_gui()
        except BaseException:  # a windowed executable has nowhere else to show this
            traceback.print_exc(file=log)
            if "--self-test" in sys.argv or "--verify-installation" in sys.argv:
                log.flush()
                os._exit(1)
            _error_dialog(f"LaTeXtify could not start.\n\nThe diagnostic log is here:\n{log_path}")


if __name__ == "__main__":
    main()
