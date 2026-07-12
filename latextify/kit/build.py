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
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from latextify.compile.tectonic import download_tectonic_release
from latextify.templates import loader

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]


@dataclass(frozen=True)
class Target:
    """One offline kit target platform."""

    name: str  # canonical kit label, e.g. "win-x64"
    os: str  # "windows" | "linux" | "macos"
    arch: str  # "x64" | "arm64"
    tectonic_triple: str  # Tectonic release asset triple
    tectonic_binary: str  # extracted binary name on the target
    #: pip ``--platform`` tags for a CROSS download; empty means "native".
    pip_platforms: tuple[str, ...]


# Cross-download platform tags list newest-first; pip accepts several and picks
# the most specific wheel each dependency actually publishes.
TARGETS: dict[str, Target] = {
    "win-x64": Target(
        "win-x64", "windows", "x64",
        "x86_64-pc-windows-msvc", "tectonic.exe",
        ("win_amd64",),
    ),
    "linux-x64": Target(
        "linux-x64", "linux", "x64",
        "x86_64-unknown-linux-gnu", "tectonic",
        ("manylinux_2_28_x86_64", "manylinux2014_x86_64",
         "manylinux_2_17_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"),
    ),
    "macos-arm64": Target(
        "macos-arm64", "macos", "arm64",
        "aarch64-apple-darwin", "tectonic",
        ("macosx_14_0_arm64", "macosx_13_0_arm64", "macosx_12_0_arm64",
         "macosx_11_0_arm64", "macosx_11_0_universal2"),
    ),
}

DEFAULT_PY_VERSIONS: tuple[str, ...] = ("3.10", "3.11", "3.12", "3.13", "3.14")


def _host_target_name() -> str:
    os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    mach = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(mach, mach)
    for target in TARGETS.values():
        if target.os == os_name and target.arch == arch:
            return target.name
    raise KitBuildError(
        f"no offline-kit target defined for this host ({os_name}-{arch}); "
        f"known targets: {', '.join(TARGETS)}"
    )


class KitBuildError(RuntimeError):
    """A recoverable, user-facing offline-kit build failure."""


def resolve_target(name: str) -> Target:
    """Map a ``--target`` value ('current' or a canonical name) to a :class:`Target`."""
    if name == "current":
        name = _host_target_name()
    try:
        return TARGETS[name]
    except KeyError:
        raise KitBuildError(
            f"unknown target {name!r}; choose one of: current, {', '.join(TARGETS)}"
        ) from None


def kit_dir_name(target: Target) -> str:
    return f"latextify-offline-{target.os}-{target.arch}"


def is_cross_build(target: Target) -> bool:
    """True when ``target`` is not the build host (needs cross pip download)."""
    return target.name != _host_target_name()


def pip_platform_args(target: Target) -> list[str]:
    """pip ``--platform`` args for a cross download (empty list for native)."""
    args: list[str] = []
    for tag in target.pip_platforms:
        args += ["--platform", tag]
    return args


def build_bundle_info(target: Target, version: str, py_versions: list[str], *, warm_tex: bool,
                      with_gui: bool, journals: list[str]) -> dict:
    """Shape the ``bundle-info.json`` manifest (pure)."""
    return {
        "name": "latextify",
        "version": version,
        "os": target.os,
        "arch": target.arch,
        "target": target.name,
        "python_versions": list(py_versions),
        "warm_tex": warm_tex,
        "with_gui": with_gui,
        "warmed_journals": sorted(journals) if warm_tex else [],
    }


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
        "uv", "export", "--frozen", "--no-dev", "--no-emit-project",
        "--no-hashes", "--format", "requirements-txt", "-o", str(req),
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
            "uv", "run", "--python", v, "--with", "pip", "--no-project",
            "python", "-m", "pip", "download", "-r", str(req), "--dest", str(wheelhouse),
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
    _run([
        "uv", "run", "--python", py_versions[-1], "--with", "pip", "--no-project",
        "python", "-m", "pip", "download", "pip", "--dest", str(wheelhouse),
        "--only-binary=:all:",
    ])

    # Native builds may fetch a sdist for a dep with no wheel; turn those into
    # wheels HERE (per covered Python) so the target never needs build tools. A
    # cross build used --only-binary, so any sdist is a genuine coverage gap.
    sdists = [p for p in wheelhouse.iterdir()
              if p.name.endswith((".tar.gz", ".tar.bz2", ".zip"))]
    for sdist in sdists:
        if cross:
            raise KitBuildError(
                f"{sdist.name} has no {target.name} wheel on PyPI -- a cross kit "
                "cannot build it here. Build this kit on a matching host, or pin a "
                "version that ships a wheel."
            )
        print(f"-- building wheel from sdist: {sdist.name} --", flush=True)
        for v in py_versions:
            _run([
                "uv", "run", "--python", v, "--with", "pip", "--no-project",
                "python", "-m", "pip", "wheel", str(sdist), "--no-deps",
                "--wheel-dir", str(wheelhouse),
            ])
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


#: Cache subtrees that are host/arch-specific engine format dumps (not TeX
#: source) -- a target regenerates these locally and offline from the cached
#: sources, so shipping them would only bloat the kit and risk a cross-arch
#: mismatch. Stripped before the warmed cache is copied into the kit.
_NONPORTABLE_CACHE_NAMES = ("formats",)

# The warm-up document body. Fonts are pulled by SIZE x WEIGHT x FAMILY, not by
# a document's metadata, so a trivial body under-warms: a real manuscript's 9pt
# abstract needs lmroman9, its 12pt-bold headings need lmroman12-bold -- neither
# is loaded by "Warm-up." at 10pt regular. Latin Modern ships discrete design
# sizes (5,6,7,8,9,10,12,17pt), each a separate font file per weight/family, and
# a class picks the nearest for any requested size. So \WarmAt renders every
# design size (plus the common in-between sizes classes ask for) in roman
# regular/bold/italic/bold-italic, small-caps, sans, and typewriter, and the
# body adds inline + display math (math font). Journal-agnostic (only
# \section/text/math/table -- every registered class is article-derived), this
# covers the font files an actual compile of any journal pulls.
_WARM_BODY = r"""
\begin{document}
\section{Cache warm-up}
\newcommand\WarmAt[1]{{\fontsize{#1}{#1}\selectfont
  reg \textbf{bold} \textit{italic} \textbf{\textit{bolditalic}} \textsc{smallcaps}
  \textsf{sans} \texttt{mono}}\par}
\WarmAt{5}\WarmAt{6}\WarmAt{7}\WarmAt{8}\WarmAt{9}\WarmAt{10}\WarmAt{10.5}%
\WarmAt{11}\WarmAt{12}\WarmAt{14}\WarmAt{17}\WarmAt{20}\WarmAt{24}
Inline math $E = mc^2$ and a display equation:
\[ \int_0^1 x^2 \, \mathrm{d}x = \tfrac{1}{3}, \qquad \alpha\beta\gamma\sum_{n=1}^{\infty}. \]
\begin{table}[htbp]
\centering
\caption{Warm-up table.}
\begin{tabular}{ll}
\toprule
Left & Right \\
\midrule
one & two \\
\bottomrule
\end{tabular}
\end{table}
\end{document}
"""


def _long_path(p: Path) -> str:
    """Windows extended-length (\\\\?\\) form so deep cache paths clear MAX_PATH.

    Tectonic caches files under 64-char content-hash names beneath
    ``bundles/data/``; a moderately deep kit output dir can push those past the
    260-char legacy limit. The ``\\\\?\\`` prefix opts a path out of that limit.
    No-op off Windows / for already-prefixed paths.
    """
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def _copy_portable_cache(src: Path, dest: Path) -> None:
    """Copy the warmed Tectonic cache into the kit, minus non-portable formats."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _long_path(src.resolve()), _long_path(dest.resolve()), dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*_NONPORTABLE_CACHE_NAMES, "*.fmt"),
    )


def _warm_tex_cache(tex_cache: Path, journals: list[str]) -> list[str]:
    """Prime ``tex_cache`` with each journal's TeX packages, using the HOST Tectonic.

    Compiles a minimal document per journal (its rendered preamble + a trivial
    body) so the exact packages each journal's class/preamble pulls land in the
    cache. Warming runs against a SHORT-path temp cache -- Tectonic's format-file
    generation can exceed Windows MAX_PATH under a deep kit output directory --
    then the TeX *source* files are copied into the kit (the host-specific engine
    format dump is dropped; the target regenerates it locally, offline, from the
    sources, which is what makes the cache cross-platform). Per-journal failure
    is a warning, not fatal. Returns the journals that warmed successfully.
    """
    import tempfile

    from latextify.compile.tectonic import compile_document, ensure_tectonic

    tex_cache.mkdir(parents=True, exist_ok=True)
    host_tectonic = ensure_tectonic()  # host binary, only to POPULATE the cache
    warmed: list[str] = []
    prev = os.environ.get("TECTONIC_CACHE_DIR")
    with tempfile.TemporaryDirectory(prefix="ltx-warm-") as tmp:
        work_cache = Path(tmp) / "c"
        work_cache.mkdir()
        os.environ["TECTONIC_CACHE_DIR"] = str(work_cache)
        try:
            for name in journals:
                try:
                    journal = loader.load(name)
                    preamble = journal.render_preamble()
                except Exception as exc:  # noqa: BLE001 - a bad journal must not kill the build
                    print(f"  ! skip warming {name}: {exc}", flush=True)
                    continue
                workdir = Path(tmp) / f"j_{name}"
                workdir.mkdir(parents=True, exist_ok=True)
                tex = workdir / "warm.tex"
                tex.write_text(preamble + _WARM_BODY, encoding="utf-8")
                vendor = journal.root / "vendor"
                vendor_dir = vendor if vendor.is_dir() else None
                # A cold cache downloads the whole TeX bundle on the first
                # compile; a transient blip there is worth one retry.
                result = compile_document(tex, tectonic_path=host_tectonic, vendor_dir=vendor_dir)
                if not result.success:
                    result = compile_document(
                        tex, tectonic_path=host_tectonic, vendor_dir=vendor_dir
                    )
                if result.success:
                    warmed.append(name)
                    print(f"  warmed {name}", flush=True)
                else:
                    tail = "\n".join(result.raw_log.splitlines()[-8:])
                    print(f"  ! warming {name} did not compile clean (packages may be "
                          f"partially cached); continuing.\n    log tail:\n{tail}", flush=True)
        finally:
            if prev is None:
                os.environ.pop("TECTONIC_CACHE_DIR", None)
            else:
                os.environ["TECTONIC_CACHE_DIR"] = prev
        if warmed:
            _copy_portable_cache(work_cache, tex_cache)
    return warmed


def _zip_kit(kit_dir: Path) -> Path:
    """Zip ``kit_dir`` alongside itself; return the archive path."""
    archive = shutil.make_archive(
        str(kit_dir), "zip", root_dir=str(kit_dir.parent), base_dir=kit_dir.name
    )
    return Path(archive)


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
        warmed = _warm_tex_cache(kit_dir / "tex-bundle-cache", warm_journals)

    shutil.copy2(HERE / "install_template.py", kit_dir / "install.py")
    shutil.copy2(HERE / "README-OFFLINE.md", kit_dir / "README-OFFLINE.md")
    (kit_dir / "bundle-info.json").write_text(
        json.dumps(
            build_bundle_info(target, version, list(python_versions),
                              warm_tex=warm_tex, with_gui=with_gui, journals=warmed),
            indent=2,
        ) + "\n",
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
