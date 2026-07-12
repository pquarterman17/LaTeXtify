"""StrEnum semantics must hold on every supported interpreter (3.10 floor).

These lock in the behaviour the report renderer and diagnostics rely on: the
model enums are real strings whose ``str()``/``format()`` render the bare value,
not ``Class.MEMBER``. On 3.11+ this exercises stdlib ``enum.StrEnum``; on the
3.10 CI leg it exercises the backport in ``latextify.model._compat``.
"""

from __future__ import annotations

import enum

from latextify.model._compat import StrEnum
from latextify.model.compile import DiagnosticSeverity
from latextify.model.figure import FigureSource
from latextify.model.preflight import Severity


def test_members_are_strings():
    assert isinstance(Severity.ERROR, str)
    assert isinstance(DiagnosticSeverity.WARNING, str)
    assert isinstance(FigureSource.MANIFEST, str)


def test_value_equality_against_plain_str():
    assert Severity.ERROR == "error"
    assert DiagnosticSeverity.WARNING == "warning"
    assert FigureSource.OVERRIDE == "override"


def test_str_and_format_render_the_value_not_the_repr():
    # The distinguishing StrEnum behaviour: a plain ``class X(str, Enum)`` would
    # render "Severity.ERROR" here. StrEnum (and the backport) render "error".
    assert str(Severity.ERROR) == "error"
    assert f"{Severity.ERROR}" == "error"
    assert f"{DiagnosticSeverity.ERROR}" == "error"
    assert format(FigureSource.EMBEDDED) == "embedded"


def test_str_methods_work_on_members():
    # The report renderer calls ``.upper()`` directly on a severity member.
    assert Severity.WARN.upper() == "WARN"
    assert FigureSource.MANIFEST.value.upper() == "MANIFEST"


def test_auto_lowercases_member_name_like_stdlib():
    class Color(StrEnum):
        RED = enum.auto()
        DARK_BLUE = enum.auto()

    assert Color.RED == "red"
    assert Color.DARK_BLUE == "dark_blue"
    assert str(Color.RED) == "red"
