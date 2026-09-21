"""File-level integrity manifests for extracted offline distributions."""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "INSTALL-MANIFEST.sha256"
_IGNORED_NAMES = {MANIFEST_NAME, "latextify-startup.log"}
_IGNORED_PARTS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


@dataclass(frozen=True)
class IntegrityResult:
    checked: int
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.changed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _included(relative: Path) -> bool:
    return relative.name not in _IGNORED_NAMES and not (_IGNORED_PARTS & set(relative.parts))


def write_manifest(root: Path, *, manifest_path: Path | None = None) -> Path:
    """Hash every shipped file beneath ``root`` using portable relative paths."""
    root = root.resolve()
    destination = manifest_path or root / MANIFEST_NAME
    lines = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _included(relative):
            lines.append(f"{sha256_file(path)}  {relative.as_posix()}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return destination


def verify_manifest(root: Path, *, manifest_path: Path | None = None) -> IntegrityResult:
    """Verify shipped files while ignoring user-created environments and caches."""
    root = root.resolve()
    source = manifest_path or root / MANIFEST_NAME
    expected: dict[str, str] = {}
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        digest, separator, name = raw.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in string.hexdigits for character in digest)
            or not name
        ):
            raise ValueError(f"invalid integrity manifest line {number}")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe integrity manifest path on line {number}")
        expected[relative.as_posix()] = digest.lower()

    missing: list[str] = []
    changed: list[str] = []
    for name, digest in expected.items():
        path = root / Path(name)
        if not path.is_file():
            missing.append(name)
        elif sha256_file(path) != digest:
            changed.append(name)

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and _included(path.relative_to(root))
    }
    unexpected = sorted(actual - set(expected))
    return IntegrityResult(
        checked=len(expected),
        missing=tuple(sorted(missing)),
        changed=tuple(sorted(changed)),
        unexpected=tuple(unexpected),
    )


def format_result(result: IntegrityResult) -> str:
    lines = [f"Checked {result.checked} shipped files."]
    if result.ok:
        lines.append("PASS: every required shipped file is present and unchanged.")
    else:
        lines.append("FAIL: the extracted package is incomplete or has changed.")
    for label, values in (
        ("Missing", result.missing),
        ("Changed", result.changed),
        ("Additional files (informational)", result.unexpected),
    ):
        if values:
            lines.append(f"{label}:")
            lines.extend(f"  - {value}" for value in values)
    return "\n".join(lines)
