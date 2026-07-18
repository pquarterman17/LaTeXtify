"""Unit tests for latextify/emit/submission.py (plan items 6-8).

Per-document layout is applied to rendered preamble TEXT, so these tests run
on strings — no docx, pandoc, or compile involved.
"""

from __future__ import annotations

import pytest

from latextify.emit.submission import (
    DocumentLayout,
    anonymize_meta,
    apply_document_layout,
    build_main_preamble,
    parse_layout_form,
    strip_acknowledgments,
)
from latextify.model.meta import Affiliation, Author, Meta

_REVTEX = "\\documentclass[aps,prb,reprint]{revtex4-2}\n\\bibliographystyle{apsrev4-2}\n"
_ARTICLE = "\\documentclass[11pt]{article}\n"


# --------------------------------------------------------------------------- #
# parse_layout_form
# --------------------------------------------------------------------------- #


def test_parse_layout_form_all_default_is_none():
    assert parse_layout_form("default", False, False) is None


def test_parse_layout_form_builds_layout():
    layout = parse_layout_form("one", True, False)
    assert layout == DocumentLayout(columns="one", line_numbers=True)


def test_parse_layout_form_rejects_unknown_columns():
    with pytest.raises(ValueError, match="columns"):
        parse_layout_form("three", False, False)


# --------------------------------------------------------------------------- #
# Column modes
# --------------------------------------------------------------------------- #


def test_revtex_one_column_swaps_reprint_for_preprint():
    out = apply_document_layout(
        _REVTEX, document_class="revtex4-2", layout=DocumentLayout(columns="one")
    )
    class_line = out.splitlines()[0]
    opts = class_line[class_line.index("[") + 1 : class_line.index("]")].split(",")
    assert opts == ["aps", "prb", "preprint"]  # reprint swapped out, order kept


def test_revtex_two_column_is_reprint_and_idempotent():
    out = apply_document_layout(
        _REVTEX, document_class="revtex4-2", layout=DocumentLayout(columns="two")
    )
    assert "\\documentclass[aps,prb,reprint]{revtex4-2}" in out


def test_generic_class_uses_standard_column_options():
    out = apply_document_layout(
        _ARTICLE, document_class="article", layout=DocumentLayout(columns="two")
    )
    assert "\\documentclass[11pt,twocolumn]{article}" in out


def test_none_layout_is_a_noop():
    assert apply_document_layout(_REVTEX, document_class="revtex4-2", layout=None) == _REVTEX


# --------------------------------------------------------------------------- #
# Line numbers + double spacing
# --------------------------------------------------------------------------- #


def test_revtex_line_numbers_use_native_class_option():
    out = apply_document_layout(
        _REVTEX, document_class="revtex4-2", layout=DocumentLayout(line_numbers=True)
    )
    assert "linenumbers" in out.splitlines()[0]
    assert "lineno" not in out.replace("linenumbers", "")  # no package fallback


def test_generic_line_numbers_use_lineno_package():
    out = apply_document_layout(
        _ARTICLE, document_class="article", layout=DocumentLayout(line_numbers=True)
    )
    assert "\\usepackage{lineno}" in out and "\\linenumbers" in out


def test_double_spacing_appends_setspace():
    out = apply_document_layout(
        _ARTICLE, document_class="article", layout=DocumentLayout(double_spacing=True)
    )
    assert "\\usepackage{setspace}" in out and "\\doublespacing" in out


# --------------------------------------------------------------------------- #
# build_main_preamble composition (layout + endfloat + insurance lines)
# --------------------------------------------------------------------------- #


def test_build_main_preamble_composes_everything():
    out = build_main_preamble(
        _REVTEX,
        document_class="revtex4-2",
        layout=DocumentLayout(columns="one", double_spacing=True),
        figures_at_end=True,
    )
    first = out.splitlines()[0]
    opts = first[first.index("[") + 1 : first.index("]")].split(",")
    assert "preprint" in opts and "reprint" not in opts
    assert "\\usepackage[nolists,tablesfirst]{endfloat}" in out
    assert "\\doublespacing" in out
    assert "hyperref" in out  # insurance still applied
    assert "\\raggedbottom" in out


# --------------------------------------------------------------------------- #
# Anonymize
# --------------------------------------------------------------------------- #


def test_anonymize_meta_replaces_identity_keeps_content():
    meta = Meta(
        title="A Title",
        authors=(Author(name="Placeholder, Pat", affiliations=(0,), email="p@x.org"),),
        affiliations=(Affiliation(name="Fictional Institute"),),
        abstract="An abstract.",
        keywords=("magnetism",),
    )
    anon = anonymize_meta(meta)
    assert [a.name for a in anon.authors] == ["Anonymous Author(s)"]
    assert anon.affiliations == ()
    assert anon.title == "A Title" and anon.abstract == "An abstract."
    assert anon.keywords == ("magnetism",)


def test_strip_acknowledgments_section():
    body = (
        "\\section{Results}\nGood stuff.\n"
        "\\section{Acknowledgments}\nWe thank the Fictional Institute.\n"
        "\\section{Conclusion}\nThe end.\n"
    )
    out, removed = strip_acknowledgments(body)
    assert removed
    assert "Fictional Institute" not in out
    assert "\\section{Conclusion}" in out and "Good stuff." in out


def test_strip_acknowledgments_environment_and_trailing_section():
    body = (
        "Text.\n\\begin{acknowledgments}\nThanks, funder XYZ.\n\\end{acknowledgments}\n"
        "\\section*{Acknowledgements}\nMore thanks.\n"
    )
    out, removed = strip_acknowledgments(body)
    assert removed
    assert "funder XYZ" not in out and "More thanks." not in out
    assert "Text." in out


def test_strip_acknowledgments_noop_without_section():
    body = "\\section{Results}\nNothing to remove.\n"
    out, removed = strip_acknowledgments(body)
    assert not removed and out == body
