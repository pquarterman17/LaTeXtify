"""Page-content-stream geometry: dark filled rectangles and text-run positions.

METADATA_PRIVACY_PLAN item #16: the failed-redaction detector in
:mod:`latextify.privacy.pdf` used to flag a page whenever it contained ANY
dark filled rectangle and ANY extractable text, anywhere on the page. That is
right for the leak it targets -- a black box drawn over live text -- and wrong
for every scientific paper's black-filled figure elements, plot markers, and
table rules, which share a page with body text they have nothing to do with.
This module answers the sharper question: does a text run actually sit
*inside* a dark rectangle's bounding box? It does that by parsing the page's
content stream operators directly rather than scanning bytes with regex,
because getting the geometry right needs the coordinate transforms the
operators carry, not just their presence.

**Why parse operators instead of pypdf's ``extract_text()`` output alone.**
``extract_text()`` gives you *what* text is on a page, not *where* -- it
concatenates runs into a string. Its ``visitor_text`` callback (used by
:func:`text_run_bboxes`) is the only way pypdf exposes per-run position, via
the text-rendering matrix in effect at each run. The rectangle side has no
equivalent public API at all: pypdf models paths as raw operator lists, so
finding "a filled black rectangle" means walking ``re`` (append rectangle)
and the paint operators (``f``/``F``/``f*``/``B``/``B*``/``b``/``b*``)
ourselves, tracking which non-stroking colour was active when the paint
happened.

**The coordinate-space gotcha.** PDF user space is y-up, origin at the page's
bottom-left corner (PDF 32000-1:2008 §8.3), not the y-down, top-left
convention of screen/image coordinates. Every operator's operands are in the
coordinate system established by the *current transformation matrix* (CTM) at
the moment the operator runs -- and the CTM changes as ``cm`` operators and
``q``/``Q`` (save/restore graphics state) execute. A rectangle drawn by
``72 700 400 18 re`` means something different depending on what ``cm``
operators preceded it; comparing two rectangles' coordinates is only valid
once both have been carried through their own CTM into the *same* space. This
module carries everything into the page's default user space -- the space
:meth:`pypdf._page.PageObject.extract_text`'s own position tracking lands in
-- so a :class:`BBox` from :func:`dark_filled_rects` and one from
:func:`text_run_bboxes` are directly comparable with no further transform.

**What "dark" means here.** A fill is treated as a redaction-style black box
by approximate luminance, not exact ``(0, 0, 0)``: ``g``/``rg``/``k`` set an
absolute colour, ``sc``/``scn`` are arity-sniffed (1 operand -> gray, 3 ->
RGB, 4 -> CMYK) since resolving the *actual* active colour space would mean
walking the page's ``/Resources/ColorSpace`` dictionary, including
``ICCBased`` alternates and ``Separation`` tint-transform functions -- out of
scope for a heuristic. A pattern fill (``scn`` with a trailing colourant
name) is deliberately treated as "not dark": this module cannot evaluate a
pattern's appearance, and guessing wrong in the flagging direction is exactly
the false-positive failure item #16 exists to fix.

**What this module does NOT decide.** Whether an overlap between a dark rect
and a text run means a *failed redaction* is a judgement call left entirely
to :mod:`latextify.privacy.pdf`, including what to do when a content stream
cannot be parsed at all (encrypted, damaged, an unusual filter) -- these
functions simply raise (``pypdf.errors.PyPdfError`` and friends propagate
un-caught) rather than guess, so the caller's fallback decision is explicit
and visible in one place instead of silently swallowed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pypdf import PageObject
from pypdf.generic import ContentStream

#: A PDF transformation matrix ``(a, b, c, d, e, f)``, representing
#:
#:     x' = a*x + c*y + e
#:     y' = b*x + d*y + f
#:
#: per PDF 32000-1:2008 §8.3.4. Concatenating two matrices with
#: :func:`_multiply` follows the spec's row-vector convention: the point is
#: transformed by the first matrix, then the second.
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

#: Non-stroking colour operators this module tracks, dispatched to the same
#: arity-sniffing interpreter (see the module docstring's "What dark means").
_FILL_COLOR_OPS = frozenset({b"g", b"rg", b"k", b"sc", b"scn"})
#: Paint operators that fill the current path (as opposed to `S`/`s`, which
#: only stroke it, or `n`, which discards it -- the usual way a rectangle is
#: used purely as a clip path via `re W n`).
_FILL_PAINT_OPS = frozenset({b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
#: Operators that end the current path without a fill -- clears any pending
#: `re` rectangles so they are not attributed to a later, unrelated fill.
_NO_FILL_PATH_END_OPS = frozenset({b"n", b"S", b"s"})
#: Fill treated as "black box" if its approximate luminance is at or below
#: this fraction of white. Broader than exact (0, 0, 0) so an anti-aliased or
#: near-black export ("0.03 0.03 0.03 rg") still counts.
_DARK_LUMINANCE = 0.2
#: Rough glyph-box shape used to turn a text run's anchor point into a small
#: bounding box, since pypdf's visitor gives a position, not per-glyph
#: metrics. Fractions of the font size: average advance width, cap height
#: above the baseline, and descender depth below it. Generic Latin-text
#: proportions -- coarse by nature, like the rest of this heuristic.
_AVG_ADVANCE_FRACTION = 0.5
_CAP_HEIGHT_FRACTION = 0.75
_DESCENDER_FRACTION = 0.25


@dataclass(frozen=True)
class BBox:
    """An axis-aligned box in page user space (PDF points, y-up)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def intersects(self, other: BBox) -> bool:
        """Whether this box and ``other`` share any area (touching doesn't count)."""
        return (
            self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1
        )


def _multiply(m: Matrix, n: Matrix) -> Matrix:
    """Concatenate two matrices: a point transformed by ``m`` then by ``n``."""
    return (
        m[0] * n[0] + m[1] * n[2],
        m[0] * n[1] + m[1] * n[3],
        m[2] * n[0] + m[3] * n[2],
        m[2] * n[1] + m[3] * n[3],
        m[4] * n[0] + m[5] * n[2] + n[4],
        m[4] * n[1] + m[5] * n[3] + n[5],
    )


def _apply(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    """Transform the point ``(x, y)`` by ``matrix``."""
    return (
        matrix[0] * x + matrix[2] * y + matrix[4],
        matrix[1] * x + matrix[3] * y + matrix[5],
    )


def _to_matrix(values: Any) -> Matrix:
    """First six elements of ``values`` as floats; raises if fewer than six.

    ``values`` is untyped because it carries two different shapes depending
    on the caller: content-stream operands (pypdf ``NumberObject``/
    ``FloatObject``, which are real ``int``/``float`` subclasses) for a ``cm``
    operator, or the plain ``list[float]`` pypdf's text-extraction visitor
    hands back for ``cm``/``tm``. ``float()`` accepts both.
    """
    a, b, c, d, e, f = (float(v) for v in values[:6])
    return (a, b, c, d, e, f)


def _fill_is_dark(operands: list[Any]) -> bool:
    """Approximate luminance test for a non-stroking colour operator's operands.

    Interprets by arity -- 1 value is DeviceGray, 3 is DeviceRGB, 4 is
    DeviceCMYK -- which covers ``g``/``rg``/``k`` exactly and ``sc``/``scn``
    for the common device colour spaces. A trailing colourant name (a pattern
    fill via ``scn``) makes an operand fail the numeric check below, which
    correctly returns "not dark": see the module docstring.
    """
    numeric: list[float] = []
    for operand in operands:
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            return False
        numeric.append(float(operand))
    if len(numeric) == 1:
        return numeric[0] <= _DARK_LUMINANCE
    if len(numeric) == 3:
        r, g, b = numeric
        return (0.299 * r + 0.587 * g + 0.114 * b) <= _DARK_LUMINANCE
    if len(numeric) == 4:
        c, m, y, k = numeric
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
        return (0.299 * r + 0.587 * g + 0.114 * b) <= _DARK_LUMINANCE
    return False


@dataclass
class _GraphicsState:
    ctm: Matrix
    fill_dark: bool


def dark_filled_rects(contents: ContentStream) -> list[BBox]:
    """Bounding boxes, in page user space, of every solidly-filled dark rectangle.

    Walks ``contents.operations`` once, maintaining the CTM and non-stroking
    colour across ``q``/``Q`` graphics-state save/restore the same way a PDF
    consumer must (PDF 32000-1:2008 §8.4.2): a rectangle appended by ``re`` is
    only in scope until the next path-painting operator, and its device
    coordinates depend on every ``cm`` since the last ``q``.

    Raises whatever ``contents.operations`` raises (malformed operators,
    an undecodable stream) rather than catching -- see the module docstring.
    """
    stack: list[_GraphicsState] = []
    state = _GraphicsState(ctm=IDENTITY, fill_dark=False)
    pending: list[tuple[float, float, float, float]] = []
    found: list[BBox] = []

    for operands, operator in contents.operations:
        if operator == b"q":
            stack.append(_GraphicsState(state.ctm, state.fill_dark))
        elif operator == b"Q":
            if stack:
                state = stack.pop()
        elif operator == b"cm" and len(operands) >= 6:
            state.ctm = _multiply(_to_matrix(operands), state.ctm)
        elif operator == b"re" and len(operands) >= 4:
            x, y, w, h = (float(v) for v in operands[:4])
            pending.append((x, y, w, h))
        elif operator in _FILL_COLOR_OPS:
            state.fill_dark = _fill_is_dark(operands)
        elif operator in _FILL_PAINT_OPS:
            if state.fill_dark:
                found.extend(_transform_rect(state.ctm, rect) for rect in pending)
            pending = []
        elif operator in _NO_FILL_PATH_END_OPS:
            pending = []
    return found


def _transform_rect(ctm: Matrix, rect: tuple[float, float, float, float]) -> BBox:
    x, y, w, h = rect
    corners = [
        _apply(ctm, x, y),
        _apply(ctm, x + w, y),
        _apply(ctm, x, y + h),
        _apply(ctm, x + w, y + h),
    ]
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def text_run_bboxes(page: PageObject) -> list[BBox]:
    """Approximate bounding boxes, in page user space, of the page's text runs.

    pypdf's ``extract_text(visitor_text=...)`` calls back with each run's
    *anchor point* (the text-rendering matrix at the run's start, per PDF
    32000-1:2008 §9.4.4) but not per-glyph metrics, so the box is reconstructed
    from generic Latin-text proportions of the font size rather than measured
    -- adequate for "does this text sit inside that rectangle", not for
    typesetting. See ``_AVG_ADVANCE_FRACTION`` and friends above.

    Raises whatever ``extract_text`` raises rather than catching -- see the
    module docstring.
    """
    boxes: list[BBox] = []

    def _visit(text: object, cm: object, tm: object, font_dict: object, font_size: object) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        if not isinstance(cm, list) or not isinstance(tm, list):
            return
        fs = float(font_size) if isinstance(font_size, (int, float)) else 12.0
        combined = _multiply(_to_matrix(tm), _to_matrix(cm))
        x0, y0 = combined[4], combined[5]
        scale_x = math.hypot(combined[0], combined[1]) or 1.0
        scale_y = math.hypot(combined[2], combined[3]) or 1.0
        width = len(text) * fs * _AVG_ADVANCE_FRACTION * scale_x
        top = y0 + fs * _CAP_HEIGHT_FRACTION * scale_y
        bottom = y0 - fs * _DESCENDER_FRACTION * scale_y
        x1 = x0 + width
        boxes.append(BBox(min(x0, x1), min(bottom, top), max(x0, x1), max(bottom, top)))

    page.extract_text(visitor_text=_visit)
    return boxes


def text_falls_in_dark_rect(page: PageObject) -> bool:
    """Whether any text run's box overlaps any dark-filled rectangle's box.

    Empty-content pages (no ``/Contents``) are not a leak and return ``False``
    rather than raising. Any other parsing failure propagates -- see the
    module docstring and :mod:`latextify.privacy.pdf`'s fallback.
    """
    contents = page.get_contents()
    if contents is None:
        return False
    rects = dark_filled_rects(contents)
    if not rects:
        return False
    runs = text_run_bboxes(page)
    return any(rect.intersects(run) for rect in rects for run in runs)
