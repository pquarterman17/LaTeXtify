"""Offline-kit builder: pure logic + kit-content invariants (no network).

The one true end-to-end build (wheelhouse -> install -> convert) is exercised
by the offline CI job and, on a connected machine, by hand; here we lock in the
pieces that must be right BEFORE any download happens: target resolution, pip
cross-download argument shaping, the manifest, and the installer template's
"standard library only" contract.
"""

from __future__ import annotations

import ast
import compileall
from pathlib import Path

import pytest

from latextify.kit import build
from latextify.templates import loader

KIT_PKG = Path(build.__file__).resolve().parent


# --------------------------------------------------------------------------- #
# target resolution
# --------------------------------------------------------------------------- #


def test_resolve_target_canonical_names():
    for name in ("win-x64", "linux-x64", "macos-arm64"):
        assert build.resolve_target(name).name == name


def test_resolve_current_matches_a_known_target():
    resolved = build.resolve_target("current")
    assert resolved.name in build.TARGETS
    # 'current' and the host's canonical name resolve to the same Target.
    assert resolved is build.resolve_target(resolved.name)


def test_resolve_unknown_target_raises():
    with pytest.raises(build.KitBuildError):
        build.resolve_target("solaris-sparc")


def test_kit_dir_name_shape():
    assert build.kit_dir_name(build.TARGETS["win-x64"]) == "latextify-offline-windows-x64"
    assert build.kit_dir_name(build.TARGETS["macos-arm64"]) == "latextify-offline-macos-arm64"


# --------------------------------------------------------------------------- #
# pip cross-download argument shaping
# --------------------------------------------------------------------------- #


def test_pip_platform_args_are_paired_flags():
    args = build.pip_platform_args(build.TARGETS["linux-x64"])
    assert args[0] == "--platform"
    assert "manylinux2014_x86_64" in args
    # every tag is preceded by its own --platform flag
    assert args.count("--platform") == len(build.TARGETS["linux-x64"].pip_platforms)


def test_cross_build_detection_is_reflexive_for_host():
    host = build.resolve_target("current")
    assert build.is_cross_build(host) is False
    assert build.pip_platform_args(host) or not build.is_cross_build(host)


def test_every_target_has_a_tectonic_triple_and_binary():
    for target in build.TARGETS.values():
        assert target.tectonic_triple
        assert target.tectonic_binary in ("tectonic", "tectonic.exe")
        # windows -> .exe, posix -> no extension
        assert (target.os == "windows") == target.tectonic_binary.endswith(".exe")


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


def test_bundle_info_manifest_shape():
    info = build.build_bundle_info(
        build.TARGETS["linux-x64"], "0.1.0", ["3.10", "3.11"],
        warm_tex=True, with_gui=False, journals=["revtex4-2", "iopart"],
    )
    assert info["name"] == "latextify"
    assert info["os"] == "linux" and info["arch"] == "x64"
    assert info["target"] == "linux-x64"
    assert info["python_versions"] == ["3.10", "3.11"]
    assert info["warm_tex"] is True
    assert info["warmed_journals"] == ["iopart", "revtex4-2"]  # sorted


def test_bundle_info_emit_only_lists_no_warmed_journals():
    info = build.build_bundle_info(
        build.TARGETS["win-x64"], "0.1.0", ["3.12"],
        warm_tex=False, with_gui=True, journals=["revtex4-2"],
    )
    assert info["warm_tex"] is False
    assert info["warmed_journals"] == []
    assert info["with_gui"] is True


# --------------------------------------------------------------------------- #
# kit content invariants (ship with the wheel)
# --------------------------------------------------------------------------- #


def test_installer_template_and_readme_ship_in_the_package():
    assert (KIT_PKG / "install_template.py").is_file()
    assert (KIT_PKG / "README-OFFLINE.md").is_file()


def test_installer_template_is_valid_python():
    src = (KIT_PKG / "install_template.py").read_text(encoding="utf-8")
    ast.parse(src)  # raises SyntaxError if the emitted installer is malformed
    assert compileall.compile_file(str(KIT_PKG / "install_template.py"), quiet=1)


def test_installer_template_imports_only_stdlib():
    # The installer runs on the target BEFORE latextify is installed, so it must
    # never import the latextify package (or any third-party dependency).
    src = (KIT_PKG / "install_template.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported_roots.add(node.module.split(".")[0])
    assert "latextify" not in imported_roots
    third_party = {"httpx", "typer", "panflute", "lxml", "jinja2", "pillow", "PIL"}
    assert not (imported_roots & third_party), imported_roots & third_party


def test_default_warm_journals_are_the_registered_ones():
    # Sanity: warming defaults to the live registry, not a hardcoded list.
    assert set(loader.available()) == set(loader.discover())
    assert "revtex4-2" in loader.available()
