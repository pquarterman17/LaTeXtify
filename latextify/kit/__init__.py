"""Offline install-kit builder (plans/OFFLINE_PORTABILITY_PLAN.md).

`latextify make-kit` packs LaTeXtify, its dependency wheels, a Tectonic binary,
and a pre-warmed TeX package cache into a folder that installs and runs on an
air-gapped machine with only a bare Python. See :mod:`latextify.kit.build`.
"""

from latextify.kit.build import KitBuildError, Target, make_kit

__all__ = ["KitBuildError", "Target", "make_kit"]
