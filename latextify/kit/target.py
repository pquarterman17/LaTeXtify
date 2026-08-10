"""The build-target table: platform triples, and the pure functions over them.

Split out of :mod:`latextify.kit.build` (2026-08-10) along the seam that
module's own docstring already named -- "the target table, argument
construction, and manifest shaping are pure functions so they unit-test
without a build". Nothing here touches the network, a subprocess, or the
filesystem; :mod:`latextify.kit.build` is where all of that lives.

A :class:`Target` is one (os, arch, python) the kit can be built FOR, which is
not necessarily the machine building it -- see the cross-targeting notes in
:mod:`latextify.kit.build`.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


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
        "win-x64",
        "windows",
        "x64",
        "x86_64-pc-windows-msvc",
        "tectonic.exe",
        ("win_amd64",),
    ),
    "linux-x64": Target(
        "linux-x64",
        "linux",
        "x64",
        "x86_64-unknown-linux-gnu",
        "tectonic",
        (
            "manylinux_2_28_x86_64",
            "manylinux2014_x86_64",
            "manylinux_2_17_x86_64",
            "manylinux2010_x86_64",
            "manylinux1_x86_64",
        ),
    ),
    "macos-arm64": Target(
        "macos-arm64",
        "macos",
        "arm64",
        "aarch64-apple-darwin",
        "tectonic",
        (
            "macosx_14_0_arm64",
            "macosx_13_0_arm64",
            "macosx_12_0_arm64",
            "macosx_11_0_arm64",
            "macosx_11_0_universal2",
        ),
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


def build_bundle_info(
    target: Target,
    version: str,
    py_versions: list[str],
    *,
    warm_tex: bool,
    with_gui: bool,
    journals: list[str],
) -> dict:
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
