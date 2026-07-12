"""Run example 01 end to end: generate the manuscript, then convert it.

    python run.py

Regenerates ``paper.docx`` from ``make_manuscript.py``, then invokes
``latextify convert ... --pdf`` via ``python -m latextify`` (so it works no
matter how LaTeXtify was installed). If Tectonic isn't available (no cached
binary and no network), the LaTeX project is still emitted and this script
says so clearly instead of failing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import make_manuscript

HERE = Path(__file__).parent
JOURNAL = "revtex4-2"
OUTPUT = HERE / "output"


def main() -> int:
    docx = make_manuscript.build()
    print(f"generated {docx.relative_to(HERE)}")

    cmd = [
        sys.executable, "-m", "latextify", "convert", str(docx),
        "--journal", JOURNAL, "--output", str(OUTPUT), "--pdf",
    ]
    print(f"$ {' '.join(cmd)}\n")
    completed = subprocess.run(cmd)

    project = OUTPUT / JOURNAL
    pdf = project / "main.pdf"
    if pdf.is_file():
        print(f"\nPDF: {pdf}")
        return 0

    # Emit always succeeds; only the optional --pdf compile needs Tectonic.
    print(
        f"\nLaTeX project emitted at {project}"
        f" (main.tex, generated/, figures/, references.bib)."
        "\nThe PDF step needs Tectonic; if this machine is offline and has no"
        "\ncached engine, compile later with:  latextify convert ... --pdf"
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
