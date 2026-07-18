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
    "latextify/emit/project.py": 1000,
    "latextify/gui/server.py": 1021,
    "latextify/ingest/filters.py": 1061,
    "latextify/ingest/metadata_guess.py": 919,
    "latextify/cli.py": 517,
    "latextify/citations/plaintext.py": 637,
    "latextify/templates/loader.py": 539,
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
