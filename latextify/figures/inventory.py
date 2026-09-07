"""What each figure actually *is*: vector or raster, how big, how sharp in print.

The override machinery (:mod:`latextify.figures.override`) has always been able
to swap a user's vector file in for a pasted screenshot. What it could not do
was tell you anything about the result: which figure is number 3, whether the
file that won is vector, or whether the screenshot you left in place will print
at 90 DPI. This module answers those questions, and three callers consume it:

    * ``latextify figures <docx>`` -- the listing, so a figure can be named
      ``fig3.pdf`` with certainty rather than by guessing document order.
    * ``latextify convert --vector-figures`` -- a warning per figure that is
      still raster below the print threshold.
    * the consolidated report and the GUI, which show the same facts.

It also owns the one measurement path both it and
:mod:`latextify.emit.figures_copy` need. Before this module existed, the wide
figure check measured with Pillow only, so a PDF -- the format the override
order *prefers* -- could not be measured at all and every vector figure
silently fell back to a single-column float, however landscape it was.
:func:`measure` reads a PDF's page box through pypdf (already a dependency for
stapling) and rasters through Pillow, so both paths agree.

The "raster inside a PDF" case is called out separately on purpose. Printing a
screenshot to PDF is the single most common way an author believes they have
supplied vector art when they have not; the file passes every extension check
and still prints as pixels. :func:`classify` looks for the signature -- image
data, no extractable text, and a content stream short enough to be a bare
image placement -- and reports it as such, hedged ("appears to be") because the
check reads structure rather than rendering.

Two details exist because getting them wrong silently misreports resolution,
which is the one number this module is for:

    * a Word display crop shrinks the file that ships (the emitter trims the
      hidden pixels), so :func:`describe` applies it -- see :func:`_cropped`;
    * ``/Rotate`` is applied when a PDF page is rendered, so a portrait page
      marked ``/Rotate 90`` is measured landscape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from latextify.model._compat import StrEnum
from latextify.model.figure import Figure, FigureSource

#: Extensions whose content is drawn from geometry and so scales losslessly.
#: EMF/WMF are Word's own vector formats -- a chart pasted from Excel or Origin
#: via Paste Special lands as one of these (see :func:`latextify.figures.vector.
#: convert_metafile`), which is exactly the case an author cannot tell apart
#: from a screenshot by looking at the document.
VECTOR_EXTENSIONS = frozenset({".pdf", ".eps", ".svg", ".emf", ".wmf"})

#: Extensions that are a fixed grid of pixels; enlarging them past their
#: capture size is what produces a soft or blocky figure in the final PDF.
RASTER_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp"})

#: A figure whose width-to-height ratio meets this is emitted as the journal's
#: wide float (usually ``figure*``) so it spans both columns of a two-column
#: layout instead of being squeezed unreadably into one. 1.3 sits between
#: portrait/near-square single-panel plots and the landscape multi-panel
#: composites that dominate real papers. Owned here rather than in
#: :mod:`latextify.emit.figures_copy` so the listing predicts the same layout
#: the emitter will actually produce.
WIDE_ASPECT_THRESHOLD = 1.3

#: Printed width in inches assumed when reporting effective DPI. These are
#: reference widths for a two-column research journal -- roughly one column,
#: and roughly the full text block for a figure wide enough to span both --
#: not a promise about any particular journal's exact measure. They exist to
#: turn "1200 pixels" into a number an author can act on.
SINGLE_COLUMN_INCHES = 3.4
WIDE_COLUMN_INCHES = 7.0

#: Effective DPI below which a raster figure is reported as too low. 300 is the
#: floor essentially every publisher states for halftone/photographic figures.
MIN_PRINT_DPI = 300

#: PDF user-space units per inch. Fixed by the PDF specification.
_POINTS_PER_INCH = 72.0


class FigureKind(StrEnum):
    """What the winning figure file turned out to be.

    VECTOR -- geometry that scales losslessly (PDF, EPS, SVG, EMF, WMF).
    RASTER -- a fixed pixel grid (PNG, JPEG, TIFF, ...).
    RASTER_IN_PDF -- a PDF whose content appears to be a single embedded
        image and no text: a screenshot printed to PDF. It has a vector
        file's extension and a raster's limits, so it is neither of the
        above and is worth naming.
    UNKNOWN -- an extension not recognised, or a file that could not be read.
    """

    VECTOR = "vector"
    RASTER = "raster"
    RASTER_IN_PDF = "raster-in-pdf"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Measurement:
    """A figure file's intrinsic size.

    ``width``/``height`` are pixels for a raster and PDF points for a PDF.
    ``pixel_width`` is set only when real pixels are known -- for a raster that
    is its width; for a PDF holding one embedded image it is that image's
    width, which is what makes an effective-DPI figure meaningful for a
    screenshot someone printed to PDF. It is ``None`` for genuine vector art,
    which has no resolution to report.
    """

    width: float
    height: float
    pixel_width: int | None = None

    @property
    def aspect(self) -> float:
        """Width-to-height ratio; 0.0 when the height is degenerate."""
        return self.width / self.height if self.height > 0 else 0.0


@dataclass(frozen=True)
class FigureFacts:
    """One figure, described for a human deciding whether to replace it.

    Attributes:
        number: 1-based figure number in document order -- the number that
            names an override file (``figures/fig<number>.pdf``).
        caption: caption text found for the figure, or the empty string.
        source: which override tier supplied :attr:`path` (embedded media,
            a folder-convention file, or a ``figures.yaml`` entry).
        path: the file that currently wins override resolution.
        kind: what that file turned out to be (see :class:`FigureKind`).
        measurement: its intrinsic size, or ``None`` when unreadable.
        wide: whether it is landscape enough to span both columns, which
            also decides which reference width :attr:`dpi` assumes.
        in_table: the image sits in a table cell, so it is emitted inline
            rather than as a float and carries no caption.
    """

    number: int
    caption: str
    source: FigureSource
    path: Path
    kind: FigureKind
    measurement: Measurement | None
    wide: bool
    in_table: bool

    @property
    def print_width_inches(self) -> float:
        """Reference printed width this figure's DPI is reported against."""
        return WIDE_COLUMN_INCHES if self.wide else SINGLE_COLUMN_INCHES

    @property
    def dpi(self) -> float | None:
        """Effective DPI at :attr:`print_width_inches`, or ``None`` for vector.

        Genuine vector art has no resolution, so it has no DPI to report --
        that is the point of supplying it.
        """
        if self.measurement is None or self.measurement.pixel_width is None:
            return None
        return self.measurement.pixel_width / self.print_width_inches

    @property
    def is_vector(self) -> bool:
        """True only for art that really scales -- a wrapped raster does not."""
        return self.kind is FigureKind.VECTOR

    @property
    def needs_attention(self) -> bool:
        """True when this figure would benefit from a vector replacement.

        Either it is not vector at all, or it is a PDF that only looks like
        one. A resolution we could not measure is not flagged: warning about
        a file we failed to read would be guessing.
        """
        if self.is_vector:
            return False
        if self.kind is FigureKind.UNKNOWN:
            return False
        dpi = self.dpi
        return dpi is None or dpi < MIN_PRINT_DPI


def _raster_measurement(path: Path) -> Measurement | None:
    """Measure a raster's pixel dimensions with Pillow, or ``None`` on failure."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
    except Exception:  # Pillow's failure modes vary; an unreadable file is not fatal
        return None
    if width <= 0 or height <= 0:
        return None
    return Measurement(width=float(width), height=float(height), pixel_width=width)


def _pdf_first_page(path: Path):  # noqa: ANN202 -- pypdf PageObject, imported lazily
    """The first page of ``path``, or ``None`` if it cannot be read as a PDF."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return reader.pages[0] if reader.pages else None
    except Exception:  # encrypted, truncated, or not a PDF at all
        return None


def _page_images(page: object) -> list[object]:
    """Image XObjects declared in ``page``'s resources (empty when none/unreadable)."""
    try:
        resources = page["/Resources"]  # type: ignore[index]
        xobjects = resources["/XObject"].get_object()  # type: ignore[index]
    except Exception:  # a page may legitimately declare neither
        return []
    images = []
    for ref in xobjects.values():  # type: ignore[union-attr]
        try:
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                images.append(obj)
        except Exception:  # one bad entry must not hide the rest
            continue
    return images


def _page_has_text(page: object) -> bool:
    """True when the page actually draws text.

    VERIFIED (2026-09-05): a declared ``/Font`` resource proves nothing --
    reportlab, and Word's own "print to PDF", emit ``/Font`` on a page holding
    only an image. So this extracts text rather than reading the resource
    dictionary. On any doubt it answers True, keeping the raster-in-PDF check
    below conservative: it would rather stay quiet than accuse genuine vector
    art of being a screenshot.
    """
    try:
        return bool(page.extract_text().strip())  # type: ignore[attr-defined]
    except Exception:  # a content stream pypdf cannot parse is not evidence
        return True


#: A page whose content stream is smaller than this is doing almost nothing --
#: in practice a single "place this image here" instruction. Genuine vector art
#: is thousands of path operators. Used only to make the raster-in-PDF verdict
#: harder to reach by accident (e.g. a vector plot whose one raster inset sits
#: beside labels that were converted to outlines).
_BARE_IMAGE_CONTENT_BYTES = 4096


def _page_content_length(page: object) -> int:
    """Size in bytes of ``page``'s content stream; ``-1`` when unreadable."""
    try:
        contents = page.get_contents()  # type: ignore[attr-defined]
        return len(contents.get_data()) if contents is not None else 0
    except Exception:
        return -1


def _quarter_turns(page: object) -> int:
    """``page``'s /Rotate as a count of 90-degree turns (0-3); 0 when absent.

    /Rotate is inheritable and may be negative or beyond 360, so it is
    normalized here rather than compared literally against 90/270.
    """
    try:
        raw = page.get("/Rotate", 0)  # type: ignore[attr-defined]
        return int(raw) // 90 % 4
    except Exception:  # a malformed /Rotate is not worth failing a measurement
        return 0


def _pdf_measurement(path: Path) -> tuple[FigureKind, Measurement | None]:
    """Classify and measure a PDF: real vector art, or a wrapped screenshot.

    A PDF is reported as :attr:`FigureKind.RASTER_IN_PDF` only when all three
    signatures of a printed screenshot hold: it carries image data, it draws no
    text, and its content stream is short enough to be a bare image placement.
    It then carries the embedded image's pixel width, so its effective print
    DPI can be reported like any other raster. Anything else is vector.
    """
    page = _pdf_first_page(path)
    if page is None:
        return FigureKind.UNKNOWN, None
    try:
        box = page.mediabox  # type: ignore[attr-defined]
        width, height = float(box.width), float(box.height)
        # /Rotate is applied when the page is RENDERED, so a portrait mediabox
        # with /Rotate 90 ships landscape. Ignoring it measured such a figure
        # portrait: it lost the two-column float this module exists to restore,
        # and a wrapped screenshot's DPI was computed against the narrow
        # reference width, reporting roughly double the real number.
        if _quarter_turns(page) % 2 == 1:
            width, height = height, width
        measurement = Measurement(width=width, height=height)
    except Exception:
        measurement = None

    images = _page_images(page)
    content_length = _page_content_length(page)
    bare_image = 0 <= content_length <= _BARE_IMAGE_CONTENT_BYTES
    if not images or not bare_image or _page_has_text(page):
        return FigureKind.VECTOR, measurement
    try:
        pixel_width = max(int(image.get("/Width", 0)) for image in images)  # type: ignore[union-attr]
    except Exception:
        pixel_width = 0
    if pixel_width <= 0:
        return FigureKind.VECTOR, measurement
    if measurement is not None:
        measurement = Measurement(
            width=measurement.width, height=measurement.height, pixel_width=pixel_width
        )
    return FigureKind.RASTER_IN_PDF, measurement


def classify(path: Path | str) -> tuple[FigureKind, Measurement | None]:
    """Determine what the file at ``path`` is, and measure it.

    Dispatch is on extension, with PDF alone inspected further (see
    :func:`_pdf_measurement`). A file that cannot be read is
    :attr:`FigureKind.UNKNOWN` with no measurement rather than an exception --
    describing a figure must never be able to fail a conversion.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_measurement(path)
    if ext in RASTER_EXTENSIONS:
        measurement = _raster_measurement(path)
        return (FigureKind.RASTER if measurement else FigureKind.UNKNOWN), measurement
    if ext in VECTOR_EXTENSIONS:
        # EPS/SVG/EMF/WMF: genuinely vector, but their page geometry needs a
        # parser this project only pulls in at conversion time. Unmeasured is
        # honest -- and costs nothing, since vector art reports no DPI anyway.
        return FigureKind.VECTOR, None
    return FigureKind.UNKNOWN, None


def is_wide(path: Path | str) -> bool:
    """True when the figure at ``path`` is landscape past :data:`WIDE_ASPECT_THRESHOLD`.

    Works for PDFs as well as rasters, which is the whole reason the
    measurement lives here: the emitter's wide check used to be Pillow-only, so
    every PDF figure -- including the vector replacements this feature exists to
    encourage -- measured as un-wide and lost its two-column float. Any
    unreadable file degrades to ``False`` (single column), never an exception.
    """
    _, measurement = classify(path)
    if measurement is None:
        return False
    return measurement.aspect >= WIDE_ASPECT_THRESHOLD


def _cropped(
    measurement: Measurement | None, figure: Figure, kind: FigureKind
) -> Measurement | None:
    """Shrink ``measurement`` by the Word display crop the emitter will apply.

    Word crops an image for display (``a:srcRect``) but keeps every original
    pixel embedded, and :mod:`latextify.figures.convert` trims those hidden
    regions on the way into ``figures/``. So the file that SHIPS is smaller
    than the file measured here, and describing the uncropped original
    overstated print resolution -- a figure cropped to a third of its width
    reported three times its true DPI and passed a 300 DPI check it should
    have failed.

    Applied under exactly the conditions the emitter applies the crop itself:
    a raster (a crop cannot be baked into vector art or a PDF, which is warned
    about instead) whose file came from the document rather than from a
    deliberate user override authored against no ``srcRect``.
    """
    crop = figure.crop
    if (
        measurement is None
        or crop is None
        or not crop.is_effective()
        or kind is not FigureKind.RASTER
        or figure.source is not FigureSource.EMBEDDED
    ):
        return measurement
    width_kept = max(0.0, 1.0 - crop.left - crop.right)
    height_kept = max(0.0, 1.0 - crop.top - crop.bottom)
    if width_kept <= 0.0 or height_kept <= 0.0:  # a crop that hides everything
        return measurement
    pixel_width = measurement.pixel_width
    return Measurement(
        width=measurement.width * width_kept,
        height=measurement.height * height_kept,
        pixel_width=int(pixel_width * width_kept) if pixel_width is not None else None,
    )


def describe(figure: Figure, *, path: Path | None = None) -> FigureFacts:
    """Build the :class:`FigureFacts` record for one resolved ``figure``.

    ``path`` overrides which file is examined, and the emitter passes it: by
    then an SVG source has been converted to a PDF in the output tree, and
    describing the source would tell an author to replace a figure that is
    already vector in what actually ships. Defaults to the figure's own
    resolved source file, which is what the pre-conversion listing wants.

    A Word display crop is applied to the measurement (see :func:`_cropped`)
    so the reported size and DPI describe what ships, not the original.
    """
    path = figure.resolved_path if path is None else Path(path)
    kind, measurement = classify(path)
    measurement = _cropped(measurement, figure, kind)
    wide = (
        not figure.in_table
        and measurement is not None
        and measurement.aspect >= WIDE_ASPECT_THRESHOLD
    )
    return FigureFacts(
        number=figure.number,
        caption=figure.caption,
        source=figure.source,
        path=path,
        kind=kind,
        measurement=measurement,
        wide=wide,
        in_table=figure.in_table,
    )


def inventory(figures: tuple[Figure, ...]) -> tuple[FigureFacts, ...]:
    """Describe every figure, in ascending figure-number order."""
    return tuple(sorted((describe(f) for f in figures), key=lambda facts: facts.number))


def raster_warning(facts: FigureFacts, *, prefix: str = "") -> str:
    """The ``--vector-figures`` message for one figure that is not vector art.

    Always names the remedy with the exact filename to create, because the
    whole difficulty this feature addresses is not knowing which figure number
    a given plot corresponds to.
    """
    target = f"figures/fig{prefix}{facts.number}.pdf"
    if facts.kind is FigureKind.RASTER_IN_PDF:
        what = "appears to be a raster image wrapped in a PDF"
    else:
        what = f"is a raster image ({facts.path.suffix.lstrip('.').lower() or 'unknown format'})"
    dpi = facts.dpi
    if dpi is None:
        detail = "its print resolution could not be measured"
    else:
        detail = (
            f"about {dpi:.0f} DPI at {facts.print_width_inches:g} in wide, "
            f"below the {MIN_PRINT_DPI} DPI most journals require"
        )
    return (
        f"figure {facts.number} {what} -- {detail}. Supply a vector version as "
        f"{target} (PDF, EPS or SVG) to replace it."
    )


def raster_warnings(facts: tuple[FigureFacts, ...], *, prefix: str = "") -> tuple[str, ...]:
    """Every ``--vector-figures`` warning for a described figure set."""
    return tuple(raster_warning(f, prefix=prefix) for f in facts if f.needs_attention)
