"""Fidelity gaps 20 & 21 (surfaced by a second real manuscript, 2026-07-12).

Gap 20: figure captions authored as floating Word TEXT BOXES were dropped
(pandoc discards text-box content), so figures rendered with no caption.
Gap 21: a reference manager's own formatted bibliography, left in the body of
a FIELD-CODED document, duplicated the generated \\bibliography.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from latextify.citations.plaintext import strip_reference_section_to_eof
from latextify.emit.project import emit_project
from latextify.figures.extract import _textbox_captions

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)


def _write_docx(path: Path, body_xml: str) -> Path:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body_xml}'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("word/document.xml", document)
    return path


def _textbox(caption: str) -> str:
    return (
        "<w:p><w:r><w:pict><w:txbxContent><w:p><w:r>"
        f'<w:t xml:space="preserve">{caption}</w:t>'
        "</w:r></w:p></w:txbxContent></w:pict></w:r></w:p>"
    )


# --------------------------------------------------------------------------- #
# Gap 20 -- text-box caption extraction
# --------------------------------------------------------------------------- #


def test_textbox_captions_parses_fig_labels(tmp_path):
    docx = _write_docx(
        tmp_path / "boxes.docx",
        _textbox("FIG. 1: (a) Normalized MOKE loops.")
        + _textbox("Figure 2. Reflectivity with fits.")
        + _textbox("Just a floating note, not a caption."),
    )
    captions = _textbox_captions(docx)
    assert captions == {
        1: "(a) Normalized MOKE loops.",
        2: "Reflectivity with fits.",
    }


def test_textbox_captions_first_box_wins_on_duplicate(tmp_path):
    # Word emits a DrawingML box plus an identical VML fallback; keep one.
    docx = _write_docx(
        tmp_path / "dup.docx",
        _textbox("FIG. 3: SLD depth profile.") + _textbox("FIG. 3: SLD depth profile."),
    )
    assert _textbox_captions(docx) == {3: "SLD depth profile."}


def test_textbox_captions_handle_supplemental_prefix(tmp_path):
    # Supplement manuscripts label captions "Supplemental Fig. N:" (gap 22).
    docx = _write_docx(
        tmp_path / "si.docx",
        _textbox("Supplemental Fig. 1: Wide angle XRD of the 48 nm samples.")
        + _textbox("Supplementary Figure 2. Spin asymmetry data."),
    )
    assert _textbox_captions(docx) == {
        1: "Wide angle XRD of the 48 nm samples.",
        2: "Spin asymmetry data.",
    }


def test_looks_like_figure_caption_variants():
    from latextify.figures.extract import looks_like_figure_caption

    assert looks_like_figure_caption("Fig. 3: normal")
    assert looks_like_figure_caption("Figure 4. plain")
    assert looks_like_figure_caption("Supplemental Fig. 1: XRD")
    assert looks_like_figure_caption("Figure S2 - text")
    assert not looks_like_figure_caption("Supported by Fig. 1 in a sentence of prose")
    assert not looks_like_figure_caption("Just an ordinary text box.")


def test_preflight_exempts_caption_textboxes_but_flags_others(tmp_path):
    from latextify.ingest.preflight import run_preflight

    docx = _write_docx(
        tmp_path / "mixed.docx",
        _textbox("Supplemental Fig. 1: A recovered caption.")  # exempt
        + _textbox("An orphaned note that really will be dropped.")  # flagged
        + "<w:p><w:r><w:t>Body.</w:t></w:r></w:p>",
    )
    report = run_preflight(docx)
    text_box_findings = [f for f in report.findings if f.detector == "text_box"]
    assert len(text_box_findings) == 1
    assert "orphaned note" not in text_box_findings[0].message  # generic message, not the text


def test_textbox_captions_absent_yields_empty(tmp_path):
    docx = _write_docx(tmp_path / "plain.docx", "<w:p><w:r><w:t>No text boxes.</w:t></w:r></w:p>")
    assert _textbox_captions(docx) == {}


def test_textbox_captions_bad_file_is_not_fatal(tmp_path):
    not_a_docx = tmp_path / "broken.docx"
    not_a_docx.write_text("not a zip", encoding="utf-8")
    assert _textbox_captions(not_a_docx) == {}


# --------------------------------------------------------------------------- #
# Gap 21 -- strip a reference manager's bibliography from a field-coded body
# --------------------------------------------------------------------------- #


def test_strip_to_eof_cuts_from_section_heading():
    tex = "Body text.\n\n\\section{REFERENCES}\n\n{[}1{]} A. Author, Title, J 1, 1 (2020).\n"
    assert strip_reference_section_to_eof(tex) == "Body text.\n"


def test_strip_to_eof_cuts_from_bare_bold_heading():
    tex = "Body text.\n\n\\textbf{References}\n\n{[}1{]} A. Author, Title.\n"
    assert strip_reference_section_to_eof(tex) == "Body text.\n"


def test_strip_to_eof_noop_without_reference_heading():
    tex = "Body text.\n\n\\section{Discussion}\n\nMore prose.\n"
    assert strip_reference_section_to_eof(tex) == tex


def _zotero_field(display: str) -> str:
    payload = (
        '{"citationItems":[{"itemData":{"type":"article-journal",'
        '"title":"A synthetic magnon paper","container-title":"Test Physics",'
        '"DOI":"10.1000/synthetic","volume":"1","page":"1-2",'
        '"author":[{"family":"Tester","given":"T."}],'
        '"issued":{"date-parts":[[2020]]}}}]}'
    )
    instr = " ADDIN ZOTERO_ITEM CSL_CITATION " + payload + " "
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{instr}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        f'<w:r><w:t xml:space="preserve">{display}</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def test_field_coded_body_strips_the_managers_bibliography(tmp_path):
    # A field-coded manuscript whose Word plugin also left a formatted
    # REFERENCES list in the body: emit must keep ONE list (the generated
    # \bibliography), not two.
    docx = _write_docx(
        tmp_path / "fieldcoded.docx",
        f'<w:p><w:r><w:t xml:space="preserve">Magnons carry spin </w:t></w:r>{_zotero_field("[1]")}'
        '<w:r><w:t xml:space="preserve">.</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>REFERENCES</w:t></w:r></w:p>"
        '<w:p><w:r><w:t xml:space="preserve">[1] T. Tester, A synthetic magnon '
        'paper, Test Physics 1, 1 (2020).</w:t></w:r></w:p>',
    )
    result = emit_project(docx, "revtex4-2", tmp_path / "out", report=False)

    body = result.body_tex_path.read_text(encoding="utf-8")
    # The extracted field-code entry is in references.bib (rendered via \bibliography).
    assert "10.1000/synthetic" in result.bib_path.read_text(encoding="utf-8")
    # The typed list the plugin left in the body is gone (no duplicate).
    assert "REFERENCES" not in body
    assert "A synthetic magnon paper" not in body
    # A warning names what was removed.
    assert any("formatted bibliography" in w.message for w in result.warnings)
