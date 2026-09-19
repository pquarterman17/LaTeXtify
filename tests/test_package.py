"""Smoke test: the package skeleton imports and every subpackage resolves."""

import importlib
import re
from pathlib import Path

import latextify

SUBPACKAGES = [
    "ingest",
    "model",
    "citations",
    "figures",
    "templates",
    "emit",
    "compile",
    "report",
]


def test_version():
    assert latextify.__version__


def test_version_matches_pyproject():
    """`__version__` must be the declared version, not a stale literal.

    v0.2.0 shipped reporting "0.1.0" because this attribute was a third,
    unguarded copy of the version. It is now read from package metadata; this
    fails if that regresses or if the dev install is stale (re-run `uv sync`).
    """
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"', pyproject)
    assert match is not None
    assert latextify.__version__ == match.group(1)


def test_subpackages_import():
    for name in SUBPACKAGES:
        assert importlib.import_module(f"latextify.{name}")
