"""Build-time cache warmer for the demo Space image (run from the Dockerfile).

Downloads the Tectonic binary (pinned + checksum-verified inside
``latextify.compile.tectonic``) and compiles one minimal document per common
document class so Tectonic's LaTeX package cache is baked into the image.

A single failed class is logged and skipped (runtime fetches whatever is
missing on demand), but if NO class compiles the build FAILS: that means the
Tectonic binary cannot run in this image at all -- e.g. a missing shared
library on a slim base -- and every runtime compile would fail identically.
Shipping that image just moves the error somewhere harder to see (the first
demo visitor's report, with no build log to point at).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from latextify.compile.tectonic import compile_document, ensure_tectonic

# article covers the generic path; revtex4-2 pulls the REVTeX tree every APS /
# AIP journal variant shares, which is most of what demo visitors will pick.
_CLASSES = ("article", "revtex4-2")

_TEX = "\\documentclass{%s}\n\\begin{document}\nCache warm-up.\n\\end{document}\n"


def main() -> int:
    tectonic = ensure_tectonic()
    print(f"tectonic binary: {tectonic}")
    warmed = 0
    for cls in _CLASSES:
        with tempfile.TemporaryDirectory() as td:
            tex = Path(td) / "main.tex"
            tex.write_text(_TEX % cls, encoding="utf-8")
            try:
                result = compile_document(tex, tectonic_path=tectonic)
            except Exception as exc:
                print(f"warming {cls} failed: {exc}")
                continue
            if result.success:
                warmed += 1
                print(f"warmed {cls}")
            else:
                tail = "\n".join(result.raw_log.strip().splitlines()[-10:])
                print(f"warming {cls} failed (returncode {result.returncode}):\n{tail}")
    if warmed == 0:
        print(
            "FATAL: no document class compiled -- Tectonic cannot run in this "
            "image (missing shared libraries?). Refusing to build a demo whose "
            "every compile would fail."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
