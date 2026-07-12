"""Run example 03 end to end: build both documents, then convert with --supplement.

    python run.py

Regenerates ``main.docx``, ``supplement.docx``, and ``paper.yaml``, then runs
``latextify convert main.docx --supplement supplement.docx --pdf`` via
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
    make_manuscript.build()
    print("generated main.docx + supplement.docx + paper.yaml")

    cmd = [
        sys.executable, "-m", "latextify", "convert", str(HERE / "main.docx"),
        "--journal", JOURNAL, "--supplement", str(HERE / "supplement.docx"),
        "--output", str(OUTPUT), "--pdf",
    ]
    print(f"$ {' '.join(cmd)}\n")
    completed = subprocess.run(cmd)

    project = OUTPUT / JOURNAL
    main_pdf = project / "main.pdf"
    supplement_pdf = project / "supplement.pdf"
    print(f"\nreferences.bib + de-duplication summary: see {project / 'report.md'}")
    if main_pdf.is_file():
        print(f"main PDF:       {main_pdf}")
        if supplement_pdf.is_file():
            print(f"supplement PDF: {supplement_pdf}")
        return 0
    print(
        f"LaTeX project emitted at {project} (main.tex + supplement.tex);"
        "\nthe PDF step needs Tectonic. Compile later where it is available."
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
