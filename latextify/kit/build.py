"""Build a self-contained offline install kit (run on a CONNECTED machine).

The kit is a folder an air-gapped machine can install and run LaTeXtify from
with **no internet, no compiler, and only a bare Python** — the
quantized_matlab "unzip and run" philosophy, adapted to a stack that carries
compiled wheels plus two external binaries (pandoc rides inside the
pypandoc-binary wheel; Tectonic is fetched as a target-platform binary):

    latextify-offline-<os>-<arch>/
      install.py           stdlib-only installer (see install_template.py)
      README-OFFLINE.md     target-machine instructions
      bundle-info.json      os/arch/python + version manifest
      requirements.txt      exact pinned versions (provenance / IT review)
      wheelhouse/           latextify wheel + all deps (per covered Python) + pip
      tectonic/             the target-platform Tectonic binary
      tex-bundle-cache/     pre-warmed TeX packages (omitted with --no-warm-tex)

Cross-targeting: the builder can produce a kit for a platform OTHER than the
build host. Dependency wheels for a cross target are fetched with pip's
``--platform``/``--only-binary=:all:`` (run under an interpreter of the target's
Python version, fetched on demand by uv, so ``python_version`` markers resolve
correctly); the Tectonic binary is fetched for the target's release triple. Any
dependency lacking a wheel for the target fails the build LOUDLY rather than
producing a silently incomplete kit.

Design: everything network/subprocess-driven lives in :func:`make_kit`; the
target table, argument construction, and manifest shaping are pure functions so
they unit-test without a build. The one real end-to-end check is a
current-platform kit install (see tests + the offline CI job).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from latextify.compile.tectonic import download_tectonic_release
from latextify.kit.target import (
    DEFAULT_PY_VERSIONS,
    KitBuildError,
    Target,
    build_bundle_info,
    is_cross_build,
    kit_dir_name,
    pip_platform_args,
    resolve_target,
)
from latextify.kit.tex_cache import long_path, warm_tex_cache
from latextify.templates import loader

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]


# --------------------------------------------------------------------------- #
# build steps (network / subprocess)
# --------------------------------------------------------------------------- #


def _run(cmd: list[str], **kw: object) -> None:
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, **kw)  # type: ignore[call-overload]
    if result.returncode != 0:
        raise KitBuildError(f"command failed (exit {result.returncode}): {' '.join(cmd[:3])} ...")


def _require_uv() -> None:
    if shutil.which("uv") is None:
        raise KitBuildError("uv not found on PATH -- install it: https://docs.astral.sh/uv/")


def _build_project_wheel(wheelhouse: Path) -> str:
    """`uv build` the latextify wheel into the wheelhouse; return its version."""
    _run(["uv", "build", "--wheel", "--out-dir", str(wheelhouse)], cwd=str(REPO_ROOT))
    (wheelhouse / ".gitignore").unlink(missing_ok=True)  # uv drops one into --out-dir
    wheels = sorted(wheelhouse.glob("latextify-*.whl"))
    if not wheels:
        raise KitBuildError("uv build produced no latextify wheel")
    # latextify-0.1.0-py3-none-any.whl -> 0.1.0
    return wheels[-1].name.split("-")[1]


def _export_requirements(req: Path, *, with_gui: bool) -> None:
    cmd = [
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--no-hashes",
        "--format",
        "requirements-txt",
        "-o",
        str(req),
    ]
    if with_gui:
        cmd += ["--extra", "gui"]
    _run(cmd, cwd=str(REPO_ROOT))


def _download_deps(
    wheelhouse: Path, req: Path, py_versions: tuple[str, ...], target: Target
) -> None:
    cross = is_cross_build(target)
    platform_args = pip_platform_args(target)
    for v in py_versions:
        print(f"-- downloading dependency wheels for Python {v} ({target.name}) --", flush=True)
        cmd = [
            "uv",
            "run",
            "--python",
            v,
            "--with",
            "pip",
            "--no-project",
            "python",
            "-m",
            "pip",
            "download",
            "-r",
            str(req),
            "--dest",
            str(wheelhouse),
        ]
        if cross:
            # cross target: wheels only (no local build possible) + retarget the
            # platform tag. Running under the target's Python version keeps
            # python_version markers correct; --platform retargets the OS/arch.
            cmd += ["--only-binary=:all:", *platform_args]
        else:
            cmd += ["--prefer-binary"]
        _run(cmd)

    # a universal pip wheel so install.py can bootstrap pip on ensurepip-less targets
    _run(
        [
            "uv",
            "run",
            "--python",
            py_versions[-1],
            "--with",
            "pip",
            "--no-project",
            "python",
            "-m",
            "pip",
            "download",
            "pip",
            "--dest",
            str(wheelhouse),
            "--only-binary=:all:",
        ]
    )

    # Native builds may fetch a sdist for a dep with no wheel; turn those into
    # wheels HERE (per covered Python) so the target never needs build tools. A
    # cross build used --only-binary, so any sdist is a genuine coverage gap.
    sdists = [p for p in wheelhouse.iterdir() if p.name.endswith((".tar.gz", ".tar.bz2", ".zip"))]
    for sdist in sdists:
        if cross:
            raise KitBuildError(
                f"{sdist.name} has no {target.name} wheel on PyPI -- a cross kit "
                "cannot build it here. Build this kit on a matching host, or pin a "
                "version that ships a wheel."
            )
        print(f"-- building wheel from sdist: {sdist.name} --", flush=True)
        for v in py_versions:
            _run(
                [
                    "uv",
                    "run",
                    "--python",
                    v,
                    "--with",
                    "pip",
                    "--no-project",
                    "python",
                    "-m",
                    "pip",
                    "wheel",
                    str(sdist),
                    "--no-deps",
                    "--wheel-dir",
                    str(wheelhouse),
                ]
            )
        sdist.unlink()

    leftovers = [p.name for p in wheelhouse.iterdir() if not p.name.endswith(".whl")]
    if leftovers:
        raise KitBuildError(
            f"non-wheel artifacts remain in the wheelhouse: {leftovers} -- the offline "
            "target could not install these without build tools"
        )


def _fetch_tectonic(target: Target, tectonic_dir: Path) -> Path:
    print(f"-- fetching Tectonic binary for {target.tectonic_triple} --", flush=True)
    return download_tectonic_release(target.tectonic_triple, target.tectonic_binary, tectonic_dir)


def _zip_kit(kit_dir: Path) -> Path:
    """Zip ``kit_dir`` alongside itself; return the archive path.

    Uses an explicit ``zipfile`` walk instead of ``shutil.make_archive``:
    ``shutil.make_archive`` stats each member through a plain (non-prefixed)
    path, and a warmed kit's ``tex-bundle-cache/bundles/data/<64-char-hash>``
    files can push the total path past Windows' 260-char MAX_PATH, making
    ``os.stat`` raise ``FileNotFoundError: [WinError 3]`` even though the
    same tree copies fine -- :func:`copy_portable_cache` already routes
    around the identical limit for the cache-copy step via :func:`long_path`.
    Produces the same ``<kit_dir.name>/<relative path>`` layout
    ``shutil.make_archive(..., root_dir=kit_dir.parent, base_dir=kit_dir.name)``
    produced, including a zip entry for every directory (so an empty
    directory still round-trips), at the same archive path.
    """
    archive_path = kit_dir.parent / f"{kit_dir.name}.zip"
    root = kit_dir.resolve()
    walk_root = long_path(root)
    with zipfile.ZipFile(long_path(archive_path.resolve()), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{kit_dir.name}/", "")
        for dirpath, dirnames, filenames in os.walk(walk_root):
            dirnames.sort()
            rel = os.path.relpath(dirpath, walk_root)
            rel_posix = "" if rel == os.curdir else rel.replace(os.sep, "/")
            base = kit_dir.name if not rel_posix else f"{kit_dir.name}/{rel_posix}"
            for name in dirnames:
                zf.writestr(f"{base}/{name}/", "")
            for name in sorted(filenames):
                zf.write(os.path.join(dirpath, name), f"{base}/{name}")
    return archive_path


def make_kit(
    target_name: str,
    *,
    python_versions: tuple[str, ...] = DEFAULT_PY_VERSIONS,
    output_dir: Path,
    warm_tex: bool = True,
    journals: list[str] | None = None,
    with_gui: bool = False,
    make_zip: bool = False,
) -> Path:
    """Build an offline kit for ``target_name`` under ``output_dir``; return the kit dir.

    ``journals`` limits TeX-cache warming (default: every registered journal).
    ``warm_tex=False`` produces a smaller emit-only kit (no ``tex-bundle-cache/``).
    ``with_gui`` adds the optional GUI dependency wheels.
    """
    _require_uv()
    target = resolve_target(target_name)
    warm_journals = sorted(journals) if journals else loader.available()

    output_dir = Path(output_dir).resolve()
    kit_dir = output_dir / kit_dir_name(target)
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    wheelhouse = kit_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True)

    version = _build_project_wheel(wheelhouse)
    _export_requirements(kit_dir / "requirements.txt", with_gui=with_gui)
    _download_deps(wheelhouse, kit_dir / "requirements.txt", python_versions, target)

    _fetch_tectonic(target, kit_dir / "tectonic")

    warmed: list[str] = []
    if warm_tex:
        warmed = warm_tex_cache(kit_dir / "tex-bundle-cache", warm_journals)

    shutil.copy2(HERE / "install_template.py", kit_dir / "install.py")
    shutil.copy2(HERE / "README-OFFLINE.md", kit_dir / "README-OFFLINE.md")
    (kit_dir / "bundle-info.json").write_text(
        json.dumps(
            build_bundle_info(
                target,
                version,
                list(python_versions),
                warm_tex=warm_tex,
                with_gui=with_gui,
                journals=warmed,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    n_wheels = sum(1 for _ in wheelhouse.glob("*.whl"))
    size_mb = sum(p.stat().st_size for p in kit_dir.rglob("*") if p.is_file()) / 1e6
    print(
        f"done: {kit_dir} ({size_mb:.0f} MB, latextify {version}, {target.name}, "
        f"{n_wheels} wheels, py {' '.join(python_versions)}"
        f"{', warmed ' + str(len(warmed)) + ' journals' if warm_tex else ', emit-only'})",
        flush=True,
    )
    if make_zip:
        archive = _zip_kit(kit_dir)
        print(f"zipped: {archive} ({archive.stat().st_size / 1e6:.0f} MB)", flush=True)
    return kit_dir
