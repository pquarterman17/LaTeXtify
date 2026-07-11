"""Smoke test: the package skeleton imports and every subpackage resolves."""

import importlib

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


def test_subpackages_import():
    for name in SUBPACKAGES:
        assert importlib.import_module(f"latextify.{name}")
