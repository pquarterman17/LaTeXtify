"""Python 3.10 compatibility shim for the model layer.

``enum.StrEnum`` only landed in Python 3.11, but the project floor is 3.10
(older lab / instrument machines -- see ``plans/OFFLINE_PORTABILITY_PLAN.md``).
Importing ``StrEnum`` from here gives the stdlib class on 3.11+ and a
behaviour-identical backport on 3.10, so the model enums keep full StrEnum
semantics on every supported interpreter:

* members are genuine ``str`` instances (``isinstance(Severity.ERROR, str)``),
* ``str(member)`` and ``f"{member}"`` yield the bare value (``"error"``), not
  the ``"Severity.ERROR"`` a plain ``class X(str, Enum)`` would print,
* ``auto()`` lower-cases the member name, matching stdlib StrEnum.

Only the model enums (``compile``/``preflight``/``figure``) import this; keeping
the shim beside them means the rest of the codebase never sees version-specific
enum behaviour.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - the backport path only runs on the 3.10 CI leg
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of :class:`enum.StrEnum` (3.11) for Python 3.10.

        Mirrors CPython's implementation: string-typed values, ``str``-based
        ``__str__``/``__format__`` so the value (not the ``Class.MEMBER`` repr)
        is what renders, and a lower-cased ``auto()`` value generator.
        """

        def __new__(cls, *values: object) -> StrEnum:
            if len(values) != 1 or not isinstance(values[0], str):
                raise TypeError("StrEnum values must be a single str")
            member = str.__new__(cls, values[0])
            member._value_ = values[0]
            return member

        __str__ = str.__str__
        __format__ = str.__format__

        @staticmethod
        def _generate_next_value_(
            name: str, start: int, count: int, last_values: list[object]
        ) -> str:
            return name.lower()


__all__ = ["StrEnum"]
