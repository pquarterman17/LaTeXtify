"""LaTeXtify offline installer — run this ON the target (air-gapped) machine.

    Windows:      py install.py        (or: python install.py)
    macOS/Linux:  python3 install.py

Creates a private virtual environment (.venv) next to this file and installs
LaTeXtify plus all dependencies from the bundled wheelhouse/ directory. Nothing
is downloaded — no internet access is needed — and nothing outside this folder
is touched. Re-running is safe (upgrades in place). Uninstall = delete this
folder.

If the kit carries a Tectonic binary (tectonic/) and a pre-warmed TeX package
cache (tex-bundle-cache/), the generated launcher points LaTeXtify at both, so
`latextify convert ... --pdf` compiles fully offline. A kit built --no-warm-tex
installs and emits LaTeX offline but needs network the first time it compiles a
PDF (Tectonic fetches its TeX packages then).

After install, launch with the generated LaTeXtify.bat (Windows) or ./latextify
(macOS/Linux) — e.g. `LaTeXtify.bat convert paper.docx -j revtex4-2 --pdf`.

This file is emitted verbatim into every kit by `latextify make-kit`; it must
stay import-free of the `latextify` package (it runs on the target BEFORE
LaTeXtify is installed) and use only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent

# lab-PC consoles are often strict cp1252; never let an unencodable character
# (e.g. a non-ASCII username in a printed path) kill an install
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def _die(msg: str) -> None:
    raise SystemExit(f"\nERROR: {msg}\n")


def _run(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        _die(f"command failed (exit {r.returncode}): {cmd[0]}")


def _load_info() -> dict:
    p = HERE / "bundle-info.json"
    if not p.is_file():
        _die(
            "bundle-info.json not found next to install.py -- run this script from "
            "inside the extracted latextify-offline folder."
        )
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _check_environment(info: dict) -> None:
    this_os = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    if this_os != info["os"]:
        _die(
            f"this kit contains {info['os']} packages but you are on {this_os} -- "
            f"use the latextify-offline-{this_os}-*.zip kit instead."
        )
    if info.get("arch") == "x64" and sys.maxsize <= 2**32:
        _die(
            "this kit contains 64-bit packages but you are running a 32-bit Python -- "
            "install a 64-bit Python and re-run."
        )
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    covered = info["python_versions"]
    if ver not in covered:
        hint = "py -3.13 install.py" if sys.platform == "win32" else "python3.13 install.py"
        _die(
            f"you are running Python {ver}, but this kit covers {', '.join(covered)}.\n"
            f"Either re-run with a covered version (e.g. `{hint}`) or install one from "
            "python.org -- the full installer works without internet."
        )


def _venv_python(venv_dir: Path) -> Path:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return venv_dir.joinpath(*sub)


def _create_venv(venv_dir: Path, wheelhouse: Path) -> Path:
    py = _venv_python(venv_dir)
    if py.is_file():
        print(f"reusing existing environment: {venv_dir}")
        return py
    print(f"creating environment: {venv_dir}")
    try:
        venv.EnvBuilder(with_pip=True, symlinks=(os.name != "nt")).create(venv_dir)
    except Exception as e:  # ensurepip missing (Debian/Ubuntu system python)
        print(f"note: venv-with-pip failed ({e}); bootstrapping pip from the wheelhouse")
        venv.EnvBuilder(with_pip=False, clear=True, symlinks=(os.name != "nt")).create(venv_dir)
        pips = sorted(wheelhouse.glob("pip-*.whl"))
        if not pips:
            _die(
                "the environment has no pip and the wheelhouse carries no pip wheel -- "
                "rebuild the kit with `latextify make-kit`."
            )
        # run pip straight out of its own wheel (zipimport) to install itself
        _run([str(py), str(pips[-1]) + os.sep + "pip", "install", "--no-index",
              "--find-links", str(wheelhouse), "pip"])
    if not py.is_file():
        _die(f"virtual environment creation failed -- no interpreter at {py}")
    return py


def _write_launchers(target: Path, venv_dir: Path) -> list[Path]:
    """Write LaTeXtify.bat / ./latextify that run the venv CLI with the in-kit
    Tectonic binary on PATH and TECTONIC_CACHE_DIR pointed at the warmed cache.

    Absolute paths are embedded so the launcher works no matter where it is
    invoked from. The Tectonic lines are emitted only when the kit actually
    carries those directories (a --no-warm-tex / emit-only kit omits them, and
    Tectonic then falls back to its own default cache + network on first PDF).
    """
    tectonic_dir = HERE / "tectonic"
    tex_cache = HERE / "tex-bundle-cache"
    made = []
    if os.name == "nt":
        bat = target / "LaTeXtify.bat"
        lines = ["@echo off"]
        if tectonic_dir.is_dir():
            lines.append(f'set "PATH={tectonic_dir};%PATH%"')
        if tex_cache.is_dir():
            lines.append(f'set "TECTONIC_CACHE_DIR={tex_cache}"')
        lines.append(f'"{venv_dir}\\Scripts\\latextify.exe" %*')
        bat.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", errors="replace")
        made.append(bat)
    else:
        sh = target / "latextify"
        lines = ["#!/bin/sh"]
        if tectonic_dir.is_dir():
            lines.append(f'export PATH="{tectonic_dir}:$PATH"')
        if tex_cache.is_dir():
            lines.append(f'export TECTONIC_CACHE_DIR="{tex_cache}"')
        lines.append(f'exec "{venv_dir}/bin/latextify" "$@"')
        sh.write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
        sh.chmod(0o755)
        made.append(sh)
    return made


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Install LaTeXtify from the bundled wheelhouse (no internet needed)."
    )
    ap.add_argument("--dir", default=str(HERE), metavar="DIR",
                    help="where to put .venv and the launcher (default: this folder)")
    args = ap.parse_args()

    info = _load_info()
    _check_environment(info)

    wheelhouse = HERE / "wheelhouse"
    if not wheelhouse.is_dir():
        _die("wheelhouse/ not found next to install.py -- extract the full zip before running.")

    target = Path(args.dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    venv_dir = target / ".venv"
    if os.name == "nt" and len(str(target)) > 140:
        # deep site-packages paths (pillow/lxml dist-info) can exceed MAX_PATH
        print("WARNING: this folder's path is quite long; Windows installs can")
        print("         fail with 'filename too long'. If that happens, extract")
        print("         to a shorter path (e.g. C:\\LaTeXtify) and re-run.")

    py = _create_venv(venv_dir, wheelhouse)
    base = [str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse)]
    if any(wheelhouse.glob("pip-*.whl")):
        # a current pip avoids old-ensurepip metadata quirks; best-effort
        subprocess.run(base + ["--upgrade", "--quiet", "pip"])
    _run(base + ["--upgrade", "latextify"])

    check = subprocess.run(
        [str(py), "-c", "import latextify; print(getattr(latextify, '__version__', '?'))"],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        _die(f"install verification failed:\n{check.stderr}")
    version = check.stdout.strip()

    launchers = _write_launchers(target, venv_dir)
    print(f"\nLaTeXtify {version} installed.")
    print("Convert a manuscript with, e.g.:")
    for launcher in launchers:
        print(f"  {launcher} convert paper.docx -j revtex4-2 --pdf")
    if not (HERE / "tex-bundle-cache").is_dir():
        print(
            "\nNote: this kit has no pre-warmed TeX cache (--no-warm-tex); the first\n"
            "      --pdf compile will need internet for Tectonic to fetch TeX packages.\n"
            "      Emitting LaTeX without --pdf works fully offline."
        )


if __name__ == "__main__":
    main()
