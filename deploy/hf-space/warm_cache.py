"""Build-time cache warmer for the demo Space image (run from the Dockerfile).

Downloads the Tectonic binary (pinned + checksum-verified inside
``latextify.compile.tectonic``) and compiles one minimal document per common
document class so Tectonic's LaTeX package cache is baked into the image.
Warming is best-effort: a failed class is logged and skipped, because at
runtime Tectonic fetches whatever is missing on demand anyway.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from latextify.compile.tectonic import compile_document, ensure_tectonic

# article covers the generic path; revtex4-2 pulls the REVTeX tree every APS /
# AIP journal variant shares, which is most of what demo visitors will pick.
_CLASSES = ("article", "revtex4-2")

_TEX = "\\documentclass{%s}\n\\begin{document}\nCache warm-up.\n\\end{document}\n"


def main() -> None:
    tectonic = ensure_tectonic()
    print(f"tectonic binary: {tectonic}")
    for cls in _CLASSES:
        with tempfile.TemporaryDirectory() as td:
            tex = Path(td) / "main.tex"
            tex.write_text(_TEX % cls, encoding="utf-8")
            try:
                result = compile_document(tex, tectonic_path=tectonic)
                print(f"warmed {cls}: success={result.success}")
            except Exception as exc:  # warming is best-effort, never fatal
                print(f"warming {cls} failed (non-fatal): {exc}")


if __name__ == "__main__":
    main()
