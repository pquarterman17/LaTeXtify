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
import os
import shutil
import zipfile
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


# --------------------------------------------------------------------------- #
# kit zipping: long-path safety on Windows (no real kit build -- fixture only)
# --------------------------------------------------------------------------- #


def _mkdirs(path: Path) -> None:
    """Create ``path`` via the same MAX_PATH escape hatch ``_zip_kit`` uses.

    Plain ``Path.mkdir`` can itself fail past Windows' 260-char limit, so the
    fixture needs the identical `\\\\?\\` prefix `_zip_kit` relies on.
    """
    os.makedirs(build._long_path(path.resolve()), exist_ok=True)


def _write(path: Path, data: bytes) -> None:
    with open(build._long_path(path.resolve()), "wb") as fh:
        fh.write(data)


def _extend_past(base: Path, floor: int) -> Path:
    """Nest ``base`` under filler segments until its path exceeds ``floor`` chars.

    Avoids hardcoding an absolute nesting depth: pytest's ``tmp_path`` prefix
    length varies by machine and test name, so the padding needed to clear
    Windows' 260-char MAX_PATH varies too.
    """
    segment = "n" * 20
    p = base
    while len(str(p)) < floor:
        p = p / segment
    return p


def test_zip_kit_round_trips_a_path_deep_enough_to_exceed_windows_max_path(tmp_path):
    """`_zip_kit` must handle a member path mirroring the real trigger:
    ``tex-bundle-cache/bundles/data/<64-char-hash>`` under a build output dir
    deep enough to push the total past Windows' 260-char MAX_PATH, which used
    to make ``shutil.make_archive`` raise ``FileNotFoundError: [WinError 3]``
    from a bare (non-prefixed) ``os.stat``. Assertions run on every platform;
    only the "actually exceeds 260" sanity check is Windows-specific, since
    other filesystems don't share that limit.
    """
    kit_dir = _extend_past(tmp_path, floor=200) / "latextify-offline-windows-x64"
    deep_dir = kit_dir / "tex-bundle-cache" / "bundles" / "data"
    deep_name = "b" * 64
    _mkdirs(deep_dir)
    _write(deep_dir / deep_name, b"deep cache entry")
    _mkdirs(kit_dir / "wheelhouse")
    _write(kit_dir / "wheelhouse" / "latextify-0.1.0-py3-none-any.whl", b"wheel bytes")
    _write(kit_dir / "bundle-info.json", b'{"name": "latextify"}')

    deep_path_len = len(str(deep_dir / deep_name))
    if os.name == "nt":
        assert deep_path_len > 260, "fixture must actually exceed MAX_PATH on Windows"

    archive = build._zip_kit(kit_dir)

    assert archive == kit_dir.parent / f"{kit_dir.name}.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        prefix = kit_dir.name
        assert (zf.read(f"{prefix}/tex-bundle-cache/bundles/data/{deep_name}")
                == b"deep cache entry")
        assert (zf.read(f"{prefix}/wheelhouse/latextify-0.1.0-py3-none-any.whl")
                == b"wheel bytes")
        assert zf.read(f"{prefix}/bundle-info.json") == b'{"name": "latextify"}'


def test_zip_kit_matches_shutil_make_archive_layout(tmp_path):
    """Lock in that the explicit zipfile walk reproduces the exact member
    layout ``shutil.make_archive(..., root_dir=.., base_dir=..)`` used to
    produce (forward-slash names, a directory entry per directory including
    empty ones, files nested under ``kit_dir.name/``) -- so a kit zipped by
    the new code still extracts the same way for anything (install docs,
    guest-run scripts) that assumes that shape.
    """
    kit_dir = tmp_path / "shallow-kit"
    (kit_dir / "sub" / "empty").mkdir(parents=True)
    (kit_dir / "a.txt").write_text("hello")
    (kit_dir / "sub" / "b.txt").write_text("world")

    archive = build._zip_kit(kit_dir)
    with zipfile.ZipFile(archive) as zf:
        got = sorted(zf.namelist())

    reference_root = tmp_path / "reference"
    shutil.copytree(kit_dir, reference_root / kit_dir.name)
    ref_archive = shutil.make_archive(
        str(reference_root / kit_dir.name), "zip",
        root_dir=str(reference_root), base_dir=kit_dir.name,
    )
    with zipfile.ZipFile(ref_archive) as zf:
        want = sorted(zf.namelist())

    assert got == want
