"""Extract Word equations, pair them with pandoc's converted LaTeX, and write
a textual side-by-side audit (plan item 23).

There is no way to render a Word equation object without Word, so the
"side-by-side render comparison" the plan calls for is textual instead of
visual: :func:`extract_equations` walks the source .docx's raw OMML
(``word/document.xml``) directly with lxml for the ground-truth equation
count and document order -- completely independent of pandoc, so a pandoc
regression shows up as a count mismatch rather than silently vanishing --
and separately asks pandoc for its own converted LaTeX (the same
docx -> JSON-AST call :mod:`latextify.ingest.pandoc` makes, but read for
``Math`` AST nodes directly rather than round-tripped through the LaTeX
writer, since that is the only way to get per-equation text instead of one
opaque body string). The two lists are paired by position; a mismatched
count is flagged on :attr:`~latextify.model.equations.EquationAuditResult.count_mismatch`
rather than guessed at silently (see :func:`extract_equations`).

:func:`write_equation_audit` is the public entry point: it writes
``equations_audit.md`` unconditionally, and -- when ``compile_pdf`` is
requested -- compiles a numbered ``audit.pdf`` via
:func:`latextify.compile.tectonic.compile_document`. Compilation uses a
two-tier strategy (see :func:`probe_compile_equations`) so one broken
equation (an unsupported OMML construct, an empty placeholder that upsets a
stricter package, ...) degrades to a visible "FAILED" block instead of
taking down the whole PDF.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import panflute as pf
import pypandoc
from lxml import etree

from latextify.citations.bib import escape_latex
from latextify.compile.tectonic import compile_document
from latextify.ingest._xml import hardened_xml_parser
from latextify.ingest.archive_guard import validate_docx_archive
from latextify.model.equations import (
    EquationAuditResult,
    EquationCompileStatus,
    EquationRecord,
    EquationWriteResult,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"w": W, "m": M}

_SNIPPET_MAX_LEN = 100

_PROBE_PREAMBLE = "\\documentclass{article}\n\\usepackage{amsmath}\n\\usepackage{amssymb}\n"


def _qn(prefixed_tag: str) -> str:
    """Expand a `prefix:local` tag name into lxml's Clark notation."""
    prefix, local = prefixed_tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def _local(element) -> str:
    """The namespace-stripped local name of an element, or "" for non-elements."""
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _snippet(text: str, max_len: int = _SNIPPET_MAX_LEN) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# Raw OMML extraction (ground truth, pandoc-independent)
# --------------------------------------------------------------------------- #


def _read_document_root(docx_path: Path | str) -> etree._Element:
    try:
        archive = zipfile.ZipFile(docx_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{docx_path}: not a valid .docx ({exc})") from exc
    with archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError(f"{docx_path}: not a valid .docx (missing word/document.xml)")
        with archive.open("word/document.xml") as fh:
            try:
                return etree.parse(fh, parser=hardened_xml_parser()).getroot()
            except etree.XMLSyntaxError as exc:
                raise ValueError(
                    f"{docx_path}: not a valid .docx "
                    f"(malformed XML in word/document.xml: {exc})"
                ) from exc


def _enclosing_paragraph(element: etree._Element) -> etree._Element | None:
    """Nearest ancestor w:p of `element`, however deeply the m: wrapper nests."""
    for ancestor in element.iterancestors(_qn("w:p")):
        return ancestor
    return None


def _paragraph_snippet_excluding_math(paragraph: etree._Element | None) -> str:
    """Containing paragraph's own text, with any oMath/oMathPara descendants
    excluded so the snippet reads as prose, not a jumble of equation runs."""
    if paragraph is None:
        return ""
    texts: list[str] = []
    for t in paragraph.iter(_qn("w:t")):
        if any(_local(a) in ("oMath", "oMathPara") for a in t.iterancestors()):
            continue
        texts.append(t.text or "")
    return _snippet("".join(texts))


def _walk_raw_equations(document_root: etree._Element) -> list[tuple[bool, str]]:
    """(display, paragraph_snippet) per ``m:oMath``, in document order.

    ``display`` is True when the element's direct parent is ``m:oMathPara``
    (Word's wrapper for a display equation); everything else is inline.
    """
    raw: list[tuple[bool, str]] = []
    for elem in document_root.iter(_qn("m:oMath")):
        parent = elem.getparent()
        display = parent is not None and _local(parent) == "oMathPara"
        snippet = _paragraph_snippet_excluding_math(_enclosing_paragraph(elem))
        raw.append((display, snippet))
    return raw


# --------------------------------------------------------------------------- #
# pandoc's own converted LaTeX per equation
# --------------------------------------------------------------------------- #


def _pandoc_math_nodes(docx_path: Path | str) -> list[pf.Math]:
    """``Math`` AST nodes from the same docx->JSON-AST pandoc call the body
    pipeline uses (see :mod:`latextify.ingest.pandoc`), in document order.

    Read directly off the AST rather than round-tripped through pandoc's
    LaTeX writer, since that is the only way to recover per-equation text
    (the writer serializes the whole body to one opaque string).
    """
    ast_json = pypandoc.convert_file(str(docx_path), to="json", format="docx")
    doc = pf.load(io.StringIO(ast_json))
    nodes: list[pf.Math] = []

    def collect(elem: pf.Element, doc: pf.Doc) -> None:
        if isinstance(elem, pf.Math):
            nodes.append(elem)

    doc.walk(collect)
    return nodes


# --------------------------------------------------------------------------- #
# Extraction: pair raw OMML with pandoc's converted LaTeX
# --------------------------------------------------------------------------- #


def extract_equations(docx_path: Path | str) -> EquationAuditResult:
    """Extract every equation in `docx_path`, paired with its converted LaTeX.

    Raw OMML order (ground truth, independent of pandoc) is paired
    positionally with pandoc's own ``Math`` AST node order. When the two
    counts agree -- the overwhelmingly common case -- every
    :class:`~latextify.model.equations.EquationRecord` carries both a real
    paragraph snippet and real converted LaTeX.

    When they disagree (:attr:`~latextify.model.equations.EquationAuditResult.count_mismatch`),
    pairing is NOT guessed at silently: indices beyond the raw count get an
    empty snippet (pandoc produced an equation with no raw-XML counterpart --
    should not normally happen, but is not dropped), and indices beyond the
    converted count get empty LaTeX (pandoc dropped or merged that equation
    away). The caller (report/CLI rendering) surfaces the mismatch loudly
    rather than pretending the pairing is trustworthy.
    """
    # Equation-audit-only operation bypasses run_preflight, so bound archive
    # resource use here too before decompressing any member.
    validate_docx_archive(docx_path)
    document_root = _read_document_root(docx_path)
    raw = _walk_raw_equations(document_root)
    math_nodes = _pandoc_math_nodes(docx_path)

    raw_count = len(raw)
    converted_count = len(math_nodes)

    equations = []
    for i in range(max(raw_count, converted_count)):
        if i < raw_count:
            display, snippet = raw[i]
        else:
            display = math_nodes[i].format == "DisplayMath"
            snippet = ""
        latex = math_nodes[i].text if i < converted_count else ""
        equations.append(
            EquationRecord(index=i, display=display, paragraph_snippet=snippet, latex=latex)
        )

    return EquationAuditResult(
        equations=tuple(equations), raw_omml_count=raw_count, converted_count=converted_count
    )


# --------------------------------------------------------------------------- #
# Probe-compile documents
# --------------------------------------------------------------------------- #


def _math_block(equation: EquationRecord) -> str:
    """Typeset one equation as real math (display or inline)."""
    if equation.display:
        return f"\\[\n{equation.latex}\n\\]"
    return f"\\({equation.latex}\\)"


def _verbatim_block(equation: EquationRecord) -> str:
    """A FAILED equation's raw LaTeX shown as literal text, never as math --
    this is what keeps one broken conversion from taking the whole audit.pdf
    down with it."""
    return f"\\begin{{verbatim}}\n{equation.latex}\n\\end{{verbatim}}"


def _equation_heading(equation: EquationRecord, *, failed: bool = False) -> str:
    kind = "display" if equation.display else "inline"
    label = f"Equation {equation.index + 1} ({kind})"
    if failed:
        label += " --- FAILED"
    snippet = escape_latex(equation.paragraph_snippet) or "(no surrounding text found)"
    return (
        f"\\par\\noindent\\textbf{{{escape_latex(label)}}}\\par\n"
        f"\\noindent\\textit{{{snippet}}}\\par\\medskip\n"
    )


def _build_probe_document(
    equations: tuple[EquationRecord, ...], failed_indices: frozenset[int]
) -> str:
    """One document with every equation, numbered and labeled with its
    source snippet. Equations in `failed_indices` render as a verbatim
    "FAILED" block (raw LaTeX shown as text) instead of real math, so the
    document as a whole always compiles once the failing set is known."""
    parts = [_PROBE_PREAMBLE, "\\begin{document}\n"]
    if not equations:
        parts.append("No equations found.\n")
    for equation in equations:
        failed = equation.index in failed_indices
        parts.append(_equation_heading(equation, failed=failed))
        parts.append((_verbatim_block if failed else _math_block)(equation))
        parts.append("\n\n")
    parts.append("\\end{document}\n")
    return "".join(parts)


def _build_single_equation_document(equation: EquationRecord) -> str:
    return (
        f"{_PROBE_PREAMBLE}\\begin{{document}}\n{_math_block(equation)}\n\\end{{document}}\n"
    )


def probe_compile_equations(
    equations: tuple[EquationRecord, ...],
    *,
    tectonic_path: Path | None = None,
    timeout: float | None = None,
) -> tuple[EquationCompileStatus, ...]:
    """Compile each equation standalone to find out which ones are broken.

    Only called after the combined all-equations document
    (:func:`_build_probe_document` with no failed indices) has already
    failed to compile once -- probing every equation individually is one
    Tectonic invocation per equation, so it is worth paying only on that
    slow path. A single bad equation's probe failing never stops the
    others from being probed; each gets its own try/isolated compile.
    """
    statuses = []
    with tempfile.TemporaryDirectory(prefix="latextify-eqprobe-") as tmp:
        tmp_dir = Path(tmp)
        for equation in equations:
            tex_path = tmp_dir / f"eq{equation.index}.tex"
            tex_path.write_text(_build_single_equation_document(equation), encoding="utf-8")
            kwargs = {"tectonic_path": tectonic_path}
            if timeout is not None:
                kwargs["timeout"] = timeout
            result = compile_document(tex_path, **kwargs)
            if result.success:
                statuses.append(EquationCompileStatus(index=equation.index, ok=True))
            else:
                message = result.errors[0].message if result.errors else "compilation failed"
                statuses.append(
                    EquationCompileStatus(index=equation.index, ok=False, message=message)
                )
    return tuple(statuses)


def _compile_audit_pdf(
    equations: tuple[EquationRecord, ...],
    output_dir: Path,
    *,
    tectonic_path: Path | None = None,
    timeout: float | None = None,
) -> tuple[Path | None, tuple[EquationCompileStatus, ...]]:
    """Two-tier compile: try every equation as real math first (one compile);
    fall back to per-equation probing only on failure, then recompile the
    final document with the identified offenders shown as text instead of
    math. Returns the compiled PDF path (None if even the corrected document
    failed) and the per-equation statuses (empty when the fast path succeeded
    -- every equation is then implicitly OK)."""
    tex_path = output_dir / "audit.tex"
    kwargs = {"tectonic_path": tectonic_path}
    if timeout is not None:
        kwargs["timeout"] = timeout

    tex_path.write_text(_build_probe_document(equations, frozenset()), encoding="utf-8")
    result = compile_document(tex_path, **kwargs)
    if result.success:
        return result.pdf_path, ()

    statuses = probe_compile_equations(equations, tectonic_path=tectonic_path, timeout=timeout)
    failed_indices = frozenset(s.index for s in statuses if not s.ok)

    tex_path.write_text(_build_probe_document(equations, failed_indices), encoding="utf-8")
    result = compile_document(tex_path, **kwargs)
    return (result.pdf_path if result.success else None), statuses


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def render_audit_markdown(
    docx_path: Path | str,
    result: EquationAuditResult,
    *,
    compile_statuses: tuple[EquationCompileStatus, ...] = (),
) -> str:
    """Render `result` (and optional compile statuses) as markdown."""
    status_by_index = {s.index: s for s in compile_statuses}
    lines: list[str] = [
        f"# Equation Audit — {Path(docx_path).name}\n",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n",
        f"**Raw OMML equations found:** {result.raw_omml_count}\n",
        f"**Pandoc-converted equations:** {result.converted_count}\n",
    ]

    if result.count_mismatch:
        lines.append(
            "\n**MISMATCH:** the raw OMML count and the pandoc-converted count "
            "disagree -- pandoc likely dropped, merged, or invented an equation. "
            "Pairing below is best-effort by document position; verify each "
            "entry against the source .docx.\n"
        )

    lines.append("\n## Equations\n")
    if not result.equations:
        lines.append("_None found._\n")
        return "".join(lines)

    for equation in result.equations:
        kind = "display" if equation.display else "inline"
        status = status_by_index.get(equation.index)
        heading = f"### Equation {equation.index + 1} ({kind})"
        if status is not None and not status.ok:
            heading += " — FAILED"
        lines.append(f"\n{heading}\n")

        snippet = equation.paragraph_snippet or "_(no surrounding text found)_"
        lines.append(f"**Source paragraph:** {snippet}\n")

        latex = equation.latex
        if not latex:
            lines.append("**Converted LaTeX:** _(empty)_\n")
        else:
            label = "raw, did not compile" if status is not None and not status.ok else "verbatim"
            lines.append(f"**Converted LaTeX** ({label}):\n")
            lines.append(f"```latex\n{latex}\n```\n")

        if status is not None and not status.ok and status.message:
            lines.append(f"**Compile error:** {status.message}\n")

    return "".join(lines)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def write_equation_audit(
    docx_path: Path | str,
    output_dir: Path | str,
    *,
    compile_pdf: bool = False,
    tectonic_path: Path | None = None,
    timeout: float | None = None,
) -> EquationWriteResult:
    """Extract equations from `docx_path` and write the audit artifacts.

    Always writes ``output_dir/equations_audit.md``. When `compile_pdf` is
    True, also compiles ``output_dir/audit.pdf`` via Tectonic using the
    two-tier strategy in :func:`_compile_audit_pdf` -- a broken equation
    degrades to a visible "FAILED" block rather than failing the whole
    audit; `output_dir` is created if missing.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = extract_equations(docx_path)

    compile_statuses: tuple[EquationCompileStatus, ...] = ()
    audit_pdf_path: Path | None = None
    if compile_pdf:
        audit_pdf_path, compile_statuses = _compile_audit_pdf(
            result.equations, output_dir, tectonic_path=tectonic_path, timeout=timeout
        )

    markdown = render_audit_markdown(docx_path, result, compile_statuses=compile_statuses)
    audit_md_path = output_dir / "equations_audit.md"
    audit_md_path.write_text(markdown, encoding="utf-8")

    return EquationWriteResult(
        audit_md_path=audit_md_path,
        audit_pdf_path=audit_pdf_path,
        result=result,
        compile_statuses=compile_statuses,
    )
