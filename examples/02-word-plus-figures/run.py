"""Run example 02 end to end: generate the manuscript + external figures, then convert.

    python run.py

Regenerates ``paper.docx``, the external ``figures/`` files, and
``figures.yaml``, then runs ``latextify convert ... --pdf`` via
``python -m latextify``. If Tectonic isn't available the LaTeX project is
still emitted and this script says so instead of failing.
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
    print(f"generated {docx.name} + figures/ + figures.yaml")

    cmd = [
        sys.executable, "-m", "latextify", "convert", str(docx),
        "--journal", JOURNAL, "--output", str(OUTPUT), "--pdf",
    ]
    print(f"$ {' '.join(cmd)}\n")
    completed = subprocess.run(cmd)

    project = OUTPUT / JOURNAL
    pdf = project / "main.pdf"
    print(f"\nfigure provenance is recorded in {project / 'report.md'} "
          "(expect Fig 1 = OVERRIDE, Fig 2 = MANIFEST).")
    if pdf.is_file():
        print(f"PDF: {pdf}")
        return 0
    print(
        f"LaTeX project emitted at {project}; the PDF step needs Tectonic."
        "\nCompile later where Tectonic is available (or use an offline kit)."
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
