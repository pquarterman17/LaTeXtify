"""Build an editable, self-contained Windows repository bundle.

The resulting archive contains the tracked source checkout, a portable CPython
runtime, and a complete offline kit. On first launch ``startup.bat`` creates a
local virtual environment solely from the bundled wheelhouse. Because it runs
``python -m latextify`` from the repository root, source edits take precedence
over the installed wheel used to seed the environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from latextify.integrity import write_manifest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "LaTeXtify-Windows-Offline-Repo"


def _die(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def _long_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        return "\\\\?\\" + resolved
    return resolved


def tracked_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def _copy_checkout(repo: Path, destination: Path) -> None:
    for relative in tracked_files(repo):
        source = repo / relative
        if not source.is_file():
            _die(f"tracked source file is missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _python_root(value: Path) -> Path:
    value = value.resolve()
    root = value.parent if value.is_file() else value
    executable = root / "python.exe"
    if not executable.is_file():
        _die(f"portable Python root has no python.exe: {root}")
    probe = subprocess.run(
        [str(executable), "-I", "-c", "import sys; print(sys.version.split()[0])"],
        check=True,
        capture_output=True,
        text=True,
    )
    if not probe.stdout.strip().startswith("3.13."):
        _die(f"expected Python 3.13, found {probe.stdout.strip()} at {executable}")
    return root


def _zip_tree(bundle_dir: Path, archive: Path) -> None:
    with zipfile.ZipFile(_long_path(archive), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        root = _long_path(bundle_dir)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            relative_dir = Path(os.path.relpath(dirpath, root))
            for name in sorted(filenames):
                source = Path(dirpath) / name
                relative = Path() if relative_dir == Path(".") else relative_dir
                zf.write(source, (Path(BUNDLE_NAME) / relative / name).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(kit: Path, python_runtime: Path, output: Path) -> Path:
    if sys.platform != "win32":
        _die("the Windows offline repository bundle must be built natively on Windows")

    kit = kit.resolve()
    info_path = kit / "bundle-info.json"
    if not info_path.is_file():
        _die(f"not an offline kit: {kit}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info.get("target") != "win-x64" or not info.get("with_gui"):
        _die("the source kit must target win-x64 and include GUI dependencies")
    if not info.get("with_dev"):
        _die("the source kit must include development dependencies (--with-dev)")

    runtime_root = _python_root(python_runtime)
    output = output.resolve()
    bundle_dir = output / BUNDLE_NAME
    if bundle_dir.exists():
        shutil.rmtree(_long_path(bundle_dir))
    bundle_dir.mkdir(parents=True, exist_ok=True)

    _copy_checkout(ROOT, bundle_dir)
    shutil.copytree(_long_path(kit), _long_path(bundle_dir / ".offline-kit"))
    shutil.copytree(_long_path(runtime_root), _long_path(bundle_dir / ".runtime" / "python"))
    shutil.copy2(
        ROOT / "packaging" / "README-WINDOWS-OFFLINE-REPO.txt",
        bundle_dir / "README-OFFLINE-REPO.txt",
    )
    shutil.copy2(
        ROOT / "packaging" / "verify_installation.py",
        bundle_dir / "verify_installation.py",
    )
    shutil.copy2(
        ROOT / "packaging" / "Verify-LaTeXtify-Repo.bat",
        bundle_dir / "Verify-LaTeXtify.bat",
    )

    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (bundle_dir / "offline-repo-info.json").write_text(
        json.dumps(
            {
                "latextify_version": info["version"],
                "source_commit": commit,
                "platform": "windows-x64",
                "python": "3.13",
                "offline": True,
                "editable_source": True,
                "development_tools": True,
                "warmed_journals": info.get("warmed_journals", []),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_manifest(bundle_dir)

    archive = output / f"{BUNDLE_NAME}.zip"
    archive.unlink(missing_ok=True)
    _zip_tree(bundle_dir, archive)
    print(f"built: {archive}")
    print(f"sha256: {_sha256(archive)}")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--python-runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("build"))
    args = parser.parse_args()
    build(args.kit, args.python_runtime, args.output)


if __name__ == "__main__":
    main()
