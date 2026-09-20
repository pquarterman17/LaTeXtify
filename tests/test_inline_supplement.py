import shutil
from pathlib import Path

from latextify.citations.body_markers import strip_reference_section_to_eof
from latextify.emit.inline_supplement import (
    BOUNDARY_MARKER,
    mark_inline_supplement,
    render_inline_supplement,
)
from latextify.emit.project import emit_project
from latextify.model import BodyConversionResult


def test_inline_supplement_adds_page_break_and_s_numbering():
    marked = mark_inline_supplement(
        "\\section{Results}\nMain.\n\\section{Supplementary Information}\nSI."
    )
    rendered = render_inline_supplement(marked)

    assert BOUNDARY_MARKER not in rendered
    assert "\\clearpage" in rendered
    assert "\\setcounter{figure}{0}" in rendered
    assert "\\renewcommand{\\thefigure}{S\\arabic{figure}}" in rendered
    assert "\\section*{Supplementary Information}" in rendered
    assert rendered.endswith("SI.")


def test_main_references_are_removed_without_removing_inline_supplement():
    marked = mark_inline_supplement(
        "Main.\n\\section{References}\nReference 1.\n\\section{Supplemental Material}\nSI content."
    )

    stripped = strip_reference_section_to_eof(marked)

    assert "Reference 1" not in stripped
    assert BOUNDARY_MARKER in stripped
    assert "SI content" in stripped


def test_inline_supplement_requires_a_recognized_heading():
    try:
        mark_inline_supplement("\\section{Results}\nNo supplement here.")
    except ValueError as exc:
        assert "no heading named Supplementary" in str(exc)
    else:
        raise AssertionError("missing supplement heading should be rejected")


def test_emit_project_writes_inline_supplement_into_main_body(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures" / "clean.docx"
    docx = tmp_path / "merged.docx"
    shutil.copy(source, docx)

    def fake_convert(_docx_path, media_dir, **_kwargs):
        return BodyConversionResult(
            tex="\\section{Results}\nMain.\n\\section{Supplementary Material}\nSI.",
            media_dir=media_dir,
            figure_count=0,
            citation_count=0,
        )

    monkeypatch.setattr("latextify.emit.project.convert_docx_to_body", fake_convert)

    result = emit_project(docx, "revtex4-2", tmp_path / "output", inline_supplement=True)
    body = result.body_tex_path.read_text(encoding="utf-8")

    assert result.supplement is None
    assert not (result.output_dir / "supplement.tex").exists()
    assert "\\clearpage" in body
    assert "\\section*{Supplementary Material}" in body
