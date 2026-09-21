"""Build the no-install Windows portable application with PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

from latextify.integrity import write_manifest

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "LaTeXtify-Windows-Portable"


def _die(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def _copy_runtime_assets(kit: Path, app_dir: Path) -> None:
    for name in ("tectonic", "tex-bundle-cache"):
        source = kit / name
        if not source.is_dir():
            _die(f"the source kit has no {name}/ directory: {source}")
        shutil.copytree(source, app_dir / name, dirs_exist_ok=True)

    shutil.copy2(ROOT / "LICENSE", app_dir / "LICENSE.txt")
    shutil.copy2(ROOT / "NOTICE", app_dir / "NOTICE.txt")
    shutil.copy2(ROOT / "packaging" / "README-WINDOWS-PORTABLE.txt", app_dir / "README-FIRST.txt")
    shutil.copy2(
        ROOT / "packaging" / "Verify-LaTeXtify-Portable.bat",
        app_dir / "Verify-LaTeXtify.bat",
    )
    sample_dir = app_dir / "sample"
    sample_dir.mkdir(exist_ok=True)
    shutil.copy2(
        ROOT / "examples" / "04-combined-emf" / "Combined-Manuscript-with-EMF.docx",
        sample_dir / "Combined-Manuscript-with-EMF.docx",
    )
    shutil.copy2(
        ROOT / "examples" / "04-combined-emf" / "README.txt",
        sample_dir / "README.txt",
    )
    shutil.copy2(
        ROOT / "examples" / "04-combined-emf" / "paper.yaml",
        sample_dir / "paper.yaml",
    )
    info = json.loads((kit / "bundle-info.json").read_text(encoding="utf-8"))
    (app_dir / "portable-info.json").write_text(
        json.dumps(
            {
                "latextify_version": info["version"],
                "platform": "windows-x64",
                "offline": True,
                "python_required": False,
                "warmed_journals": info.get("warmed_journals", []),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _zip_tree(app_dir: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(app_dir))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_previous(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    if path.exists():
        _die(f"could not remove the previous build directory: {path}")


def build(kit: Path, output: Path) -> Path:
    if sys.platform != "win32":
        _die("the portable executable must be built natively on Windows")
    kit = kit.resolve()
    if not (kit / "bundle-info.json").is_file():
        _die(f"not an offline kit: {kit}")

    output = output.resolve()
    app_dir = output / APP_NAME
    work_dir = output / "pyinstaller-work"
    spec_dir = output / "pyinstaller-spec"
    _remove_previous(app_dir)
    _remove_previous(work_dir)
    _remove_previous(spec_dir)
    output.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    PyInstaller.__main__.run(
        [
            str(ROOT / "latextify" / "portable.py"),
            "--name=LaTeXtify",
            "--onedir",
            "--windowed",
            "--clean",
            "--noconfirm",
            f"--distpath={output}",
            f"--workpath={work_dir}",
            f"--specpath={spec_dir}",
            "--collect-data=latextify",
            "--collect-all=pypandoc",
            "--collect-all=rapidfuzz",
            "--hidden-import=uvicorn.logging",
            "--hidden-import=uvicorn.loops.auto",
            "--hidden-import=uvicorn.protocols.http.auto",
            "--hidden-import=uvicorn.protocols.websockets.auto",
            "--hidden-import=uvicorn.lifespan.on",
            "--hidden-import=multipart",
        ]
    )
    frozen_dir = output / "LaTeXtify"
    if not (frozen_dir / "LaTeXtify.exe").is_file():
        _die("PyInstaller did not produce LaTeXtify.exe")
    frozen_dir.rename(app_dir)
    _copy_runtime_assets(kit, app_dir)
    write_manifest(app_dir)

    archive = output / f"{APP_NAME}.zip"
    archive.unlink(missing_ok=True)
    _zip_tree(app_dir, archive)
    print(f"built: {archive}")
    print(f"sha256: {_sha256(archive)}")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("build"))
    args = parser.parse_args()
    build(args.kit, args.output)


if __name__ == "__main__":
    main()
