"""Repo-integrity size ratchet (global size-ratchet-every-language rule).

A module-size ceiling is only useful if it covers every language and only ever
tightens. This test enforces a general per-file line ceiling on both the Python
package and the buildless frontend, with legacy files already over the ceiling
pinned at their exact ``wc -l`` size the day the ratchet landed.

Rules for the pins below:
- **Pins only move DOWN.** Extract code to shrink a file, then lower its pin.
  Never raise a pin, and never add a new one -- split the file instead.
- A pin that drops to/under the general ceiling has **graduated**: delete it.
- ~50 lines of slack keeps trivial edits from churning a pin.

The point is to make "just bump the ceiling" fail loudly and "extract to a
focused module" feel natural. When this test fails, the fix is almost always to
split a file, not to touch the numbers here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "latextify"

PY_CEILING = 500
FRONTEND_CEILING = 500
SLACK = 50

#: Legacy Python files over the ceiling, pinned at their exact wc -l size on
#: 2026-07-12 when the ratchet was added. These are standing tech debt; the
#: pins cap further growth. Lower a pin whenever you shrink its file.
PY_PINS = {
    # Every pin below was re-seeded at its exact size on 2026-08-10, after the
    # `ruff format` adoption re-measured the tree and the tech-debt pass shrank
    # several files. gui/server.py GRADUATED that day (921 -> 437, under the
    # general ceiling) and its pin is gone -- that is the ratchet working, and
    # ingest/filters.py graduated the same way (1059 -> 402).
    "latextify/ingest/metadata_guess.py": 918,
    "latextify/citations/plaintext.py": 617,
    "latextify/templates/loader.py": 515,
}

#: No frontend pins: the once-monolithic index.html graduated on 2026-07-18
#: when it was split into index.html + style.css + app.js + review.js, each
#: under the general ceiling. New frontend files must stay under it — never
#: add a pin; split the file instead.
FRONTEND_PINS: dict[str, int] = {}


def _wc_l(path: Path) -> int:
    """Physical line count matching ``wc -l`` (number of newline characters)."""
    return path.read_text(encoding="utf-8").count("\n")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _check_file(path: Path, ceiling: int, pins: dict[str, int]) -> None:
    rel = _rel(path)
    n = _wc_l(path)
    pin = pins.get(rel)

    if pin is None:
        assert n <= ceiling, (
            f"{rel} is {n} lines (> {ceiling} ceiling). Split it into focused "
            f"modules; do not add a pin unless a split is genuinely unavoidable."
        )
        return

    assert n > ceiling, (
        f"{rel} is down to {n} lines (<= {ceiling} ceiling) -- it has graduated. "
        f"Delete its pin from the ratchet."
    )
    assert n <= pin, (
        f"{rel} grew to {n} lines (> pinned {pin}). Extract enough to offset the "
        f"addition in the same change; never raise the pin."
    )
    assert n >= pin - SLACK, (
        f"{rel} is now {n} lines, well under its pin of {pin}. Lower the pin to "
        f"{n} to lock in the reduction (pins only move down)."
    )


def test_python_source_size_ratchet():
    for path in sorted(PKG.rglob("*.py")):
        _check_file(path, PY_CEILING, PY_PINS)


def test_frontend_size_ratchet():
    static = PKG / "gui" / "static"
    frontend = (
        sorted(static.rglob("*.html"))
        + sorted(static.rglob("*.js"))
        + sorted(static.rglob("*.css"))
    )
    for path in frontend:
        _check_file(path, FRONTEND_CEILING, FRONTEND_PINS)


def test_no_stale_pins():
    """A pin for a renamed/removed path would silently permit a new god file at
    that size; require every pinned path to still exist."""
    for rel in [*PY_PINS, *FRONTEND_PINS]:
        assert (ROOT / rel).is_file(), (
            f"pinned path {rel} no longer exists -- remove its stale pin."
        )


# --------------------------------------------------------------------------- #
# Version agreement (REPO_HEALTH_PLAN item 2)
# --------------------------------------------------------------------------- #

#: The project version is written in two files, and a stale ``uv.lock`` fails a
#: ``--locked`` build. Verified against the real v0.2.0 release commit
#: (2b3915a), which touched exactly these two. `tomllib` is 3.11+ and the
#: floor is 3.10, so both are read with a narrow anchored regex instead.
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^version = "([^"]+)"')
_LOCK_VERSION_RE = re.compile(r'(?m)^name = "latextify"\nversion = "([^"]+)"')


def _declared_version(path: Path, pattern: re.Pattern[str]) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"no version declaration found in {_rel(path)}"
    return match.group(1)


def test_declared_versions_agree():
    """pyproject.toml and uv.lock must record the same version.

    A release that bumps one and not the other produces a lockfile that fails
    `uv sync --locked` / `uv build --locked` -- which CI runs on every job, so
    the whole pipeline goes red at the worst possible moment. This turns that
    into a one-line local failure naming both values.
    """
    pyproject = ROOT / "pyproject.toml"
    lock = ROOT / "uv.lock"
    declared = _declared_version(pyproject, _PYPROJECT_VERSION_RE)
    locked = _declared_version(lock, _LOCK_VERSION_RE)
    assert declared == locked, (
        f"version disagreement: pyproject.toml says {declared!r}, uv.lock says "
        f"{locked!r}. Re-run `uv lock` after bumping the version, and commit both."
    )


def test_python_version_file_matches_ci():
    """`.python-version` is the single declaration CI derives from.

    Before it existed, five workflow jobs hardcoded their own Python version
    and disagreed (3.10/3.12/3.13), so a declaration could say anything and
    nothing would fail. The lint/integration jobs now read this file; the test
    matrix and the deliberately-oldest/newest kit jobs still name versions
    explicitly, because exercising a RANGE is their whole point.
    """
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert declared, ".python-version is empty"

    requires = re.search(
        r'(?m)^requires-python = ">=([\d.]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert requires is not None, "pyproject.toml declares no requires-python floor"
    floor = tuple(int(p) for p in requires.group(1).split("."))
    chosen = tuple(int(p) for p in declared.split("."))
    assert chosen >= floor, (
        f".python-version pins {declared}, below the requires-python floor "
        f"{requires.group(1)} -- the default dev interpreter cannot be older "
        f"than the oldest version the package claims to support."
    )
