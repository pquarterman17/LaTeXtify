"""Generate tests/fixtures/equations.docx.

Exercises the pandoc body pipeline (plan item 3): headings at levels 1-4
(level 4 to prove the >3 clamp in ``filters.normalize_headings`` fires) plus
inline and display OMML math (Word's equation-editor format).

python-docx cannot author OMML equations itself, so the ``<m:oMath>`` /
``<m:oMathPara>`` XML below is injected directly into paragraph runs via
python-docx's low-level ``parse_xml``/oxml API. The markup is not
hand-guessed: it is the exact OMML pandoc's own docx *writer* emits for
``$\\frac{a}{b}$`` / ``$$\\frac{a}{b}$$`` (verified by round-tripping a
markdown fixture through ``pandoc -t docx`` and inspecting
``word/document.xml``), i.e. schema-valid math identical in shape to what
Word's equation editor produces for a simple fraction.

Run with:
    uv run python tests/fixtures/make_equations.py
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

FIXTURE_PATH = Path(__file__).parent / "equations.docx"

# Real OMML for `a over b`, captured from pandoc's docx writer (see module
# docstring). `{jc}` is only present in the oMathPara (display) wrapper.
_OMATH_FRAC = (
    "<m:oMath>"
    '<m:f><m:fPr><m:type m:val="bar"/></m:fPr>'
    "<m:num><m:r><m:t>a</m:t></m:r></m:num>"
    "<m:den><m:r><m:t>b</m:t></m:r></m:den>"
    "</m:f>"
    "</m:oMath>"
)


def _append_omath(paragraph, inner_xml: str) -> None:
    """Append an inline `<m:oMath>` equation as a direct child of the paragraph."""
    xml = f"<m:oMath {nsdecls('m')}>{inner_xml}</m:oMath>"
    paragraph._p.append(parse_xml(xml))


def _append_omath_para(paragraph, inner_xml: str) -> None:
    """Append a centered display `<m:oMathPara><m:oMath>...` block."""
    xml = (
        f"<m:oMathPara {nsdecls('m')}>"
        '<m:oMathParaPr><m:jc m:val="center"/></m:oMathParaPr>'
        f"<m:oMath>{inner_xml}</m:oMath>"
        "</m:oMathPara>"
    )
    paragraph._p.append(parse_xml(xml))


def build() -> None:
    doc = Document()

    doc.add_heading("Introduction", level=1)
    doc.add_paragraph("This manuscript exercises Word equation-editor math.")

    doc.add_heading("Method", level=2)
    p = doc.add_paragraph("The result follows from ")
    # Strip the OMML's own outer <m:oMath> wrapper tag by passing only the
    # inner fraction markup through _append_omath, which re-adds the wrapper.
    inner = _OMATH_FRAC[len("<m:oMath>") : -len("</m:oMath>")]
    _append_omath(p, inner)
    p.add_run(" as shown below.")

    doc.add_heading("Results", level=3)
    doc.add_paragraph("The final expression is:")
    display_p = doc.add_paragraph()
    _append_omath_para(display_p, inner)
    doc.add_paragraph("This concludes the derivation.")

    # Heading 4 has no journal-template counterpart (classes stop at
    # \subsubsection); normalize_headings() must clamp it to level 3.
    doc.add_heading("Too Deep A Section", level=4)
    doc.add_paragraph("This paragraph lives under the clamped heading.")

    doc.save(FIXTURE_PATH)
    print(f"wrote {FIXTURE_PATH}")


if __name__ == "__main__":
    build()
