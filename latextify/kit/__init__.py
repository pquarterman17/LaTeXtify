"""Offline install-kit builder (plans/OFFLINE_PORTABILITY_PLAN.md).

`latextify make-kit` packs LaTeXtify, its dependency wheels, a Tectonic binary,
and a pre-warmed TeX package cache into a folder that installs and runs on an
air-gapped machine with only a bare Python.

Modules:
    target.py     -- the (os, arch, python) target table and the pure functions
                     over it: platform args, kit naming, manifest shaping
    build.py      -- everything network/subprocess-driven; ``make_kit`` itself
    tex_cache.py  -- pre-warming the TeX bundle cache that ships in the kit
    install_template.py -- the stdlib-only installer copied into each kit
"""

from latextify.kit.build import KitBuildError, Target, make_kit

__all__ = ["KitBuildError", "Target", "make_kit"]
