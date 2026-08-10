"""PDF metadata stripping and content-leak detection.

A PDF leaks on two levels, and they need different honesty.

**Document level** -- the ``/Info`` dictionary, the XMP packet, embedded file
attachments, document-level JavaScript, and markup annotations. These are
removable, and sanitizing removes them. Rewriting the file also discards
**incremental update history**: PDFs are appended to rather than rewritten on
save, so an "edited" PDF frequently still contains every prior revision. A
full rewrite drops them, which is one of the more valuable things this does.

**Content level** -- text hidden under a filled rectangle ("redaction" done
with a drawing tool), and content outside the CropBox. Neither can be fixed
without destructively altering the page, which needs a rasterizing dependency
this project deliberately does not carry. So they are **detected and
reported**, never silently left to look handled:

- *Failed redaction* is the classic catastrophic leak -- a black box is drawn
  over the text, the text object is untouched, and anyone can select or
  extract it. Detection (METADATA_PRIVACY_PLAN item #16) walks the page's
  content-stream operators via :mod:`latextify.privacy.pdf_content` and flags
  a page only when a text run's bounding box actually falls inside a dark
  filled rectangle's -- not merely "both exist somewhere on this page", which
  is what the original version checked and which false-positived on every
  black-filled figure, plot marker, or table rule in a scientific paper. It
  is still a heuristic (approximate glyph geometry, arity-sniffed colour
  spaces -- see the submodule) and is phrased as "possible" for that reason.
  If the content stream cannot be parsed at all (encrypted, damaged, an
  unusual filter), a false "clean" is worse than a false alarm, so detection
  falls back to the original coarse any-dark-fill-plus-any-text check; only
  if that *also* fails to run does the page turn into an explicit warning
  rather than a silent pass.
- *CropBox smaller than MediaBox* hides page content by viewport rather than
  by removal -- the exact analogue of the Word ``srcRect`` crop leak already
  fixed in :mod:`latextify.figures.crop`.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.errors import DependencyError, PdfReadError, PyPdfError
from pypdf.generic import ArrayObject, NameObject

from . import pdf_content
from .report import Finding

#: /Info keys worth naming individually, with why each matters.
_INFO_FIELDS = {
    "/Author": ("author", "high", "the document author"),
    "/Creator": (
        "software",
        "medium",
        "the application that created the source document, which can name an "
        "internal tool or template",
    ),
    "/Producer": ("software", "low", "the library that wrote the PDF"),
    "/Title": ("title", "low", "an internal working title"),
    "/Subject": ("subject", "low", "a subject line"),
    "/Keywords": ("keywords", "low", "keywords that can name an internal project"),
    "/CreationDate": ("timestamp", "low", "when the document was created"),
    "/ModDate": ("timestamp", "low", "when the document was last modified"),
}

#: Annotation subtypes that are review markup rather than document function.
#: /Link and /Widget are deliberately absent -- removing them would break
#: hyperlinks and form fields, which is a functional change, not a privacy fix.
_MARKUP_ANNOTS = frozenset(
    {
        "/Text",
        "/FreeText",
        "/Highlight",
        "/Underline",
        "/Squiggly",
        "/StrikeOut",
        "/Caret",
        "/Ink",
        "/Popup",
        "/FileAttachment",
        "/Sound",
        "/Movie",
        "/Stamp",
    }
)

#: Fallback-only: the pre-#16 coarse heuristic, kept for when the content
#: stream can be decoded to raw bytes but not walked operator-by-operator
#: (see ``_coarse_redaction_heuristic`` and the module docstring). A
#: near-black non-stroking colour: `0 g`, `0 0 0 rg`, or `0 0 0 1 k`.
_DARK_FILL_RE = re.compile(
    rb"(?:^|\s)(?:0(?:\.0+)?\s+g"
    rb"|0(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?\s+rg"
    rb"|0(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?\s+1(?:\.0+)?\s+k)(?:\s|$)"
)
_FILLED_RECT_RE = re.compile(rb"re\s+(?:f|F|f\*|b|b\*|B|B\*)(?:\s|$)")


def _read(path: Path) -> PdfReader:
    try:
        return PdfReader(str(path))
    except (PdfReadError, OSError, ValueError) as exc:
        raise ValueError(f"{path}: not a readable PDF ({exc})") from exc


def _incremental_updates(path: Path) -> int:
    """Number of prior revisions retained in the file (0 for a single-save PDF)."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    return max(0, data.count(b"%%EOF") - 1)


def _info_findings(reader: PdfReader) -> list[Finding]:
    try:
        info = reader.metadata
    except (PdfReadError, ValueError):
        return []
    if not info:
        return []
    findings = []
    for key, (category, severity, why) in _INFO_FIELDS.items():
        value = info.get(key)
        if value is None or not str(value).strip():
            continue
        findings.append(
            Finding(
                category=category,
                severity=severity,
                summary=f"{key.lstrip('/')}: {str(value)[:60]}",
                detail=f"The PDF /Info dictionary records {why}.",
                location="/Info",
            )
        )
    return findings


def _annotation_counts(reader: PdfReader) -> tuple[int, int]:
    """(markup annotations, pages carrying them)."""
    total = 0
    pages = 0
    for page in reader.pages:
        try:
            annots = page.get("/Annots")
            if not annots:
                continue
            here = 0
            for ref in annots:
                obj = ref.get_object()
                if obj.get("/Subtype") in _MARKUP_ANNOTS:
                    here += 1
            if here:
                total += here
                pages += 1
        except (PdfReadError, AttributeError, TypeError, ValueError):
            continue
    return total, pages


def _cropbox_findings(reader: PdfReader) -> list[Finding]:
    cropped = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            media, crop = page.mediabox, page.cropbox
            if (
                float(crop.width) < float(media.width) - 1
                or float(crop.height) < float(media.height) - 1
            ):
                cropped.append(index)
        except (PdfReadError, AttributeError, TypeError, ValueError):
            continue
    if not cropped:
        return []
    return [
        Finding(
            category="cropbox",
            severity="high",
            summary=f"{len(cropped)} page(s) cropped by viewport, not by removal",
            detail=(
                "The CropBox is smaller than the MediaBox, so content outside the "
                "visible area is still in the file and reappears if the crop is "
                "reset. Same shape of leak as a cropped image in Word."
            ),
            location=f"page {cropped[0]}",
            count=len(cropped),
            removable=False,
        )
    ]


#: Exceptions that mean "this page's content stream would not parse", from
#: pypdf's own hierarchy (PdfReadError covers PdfStreamError,
#: FileNotDecryptedError, EmptyFileError; PyPdfError also covers ParseError
#: and LimitReachedError; DependencyError sits outside that hierarchy) plus
#: the defensive set used throughout this module for unexpected structure.
_UNPARSEABLE_CONTENT_ERRORS = (
    PyPdfError,
    DependencyError,
    NotImplementedError,
    AttributeError,
    TypeError,
    ValueError,
    KeyError,
)


def _coarse_redaction_heuristic(page: PageObject) -> bool:
    """The pre-#16 detector: a dark fill AND extractable text, anywhere on the page.

    Used only as a fallback when :func:`~latextify.privacy.pdf_content.text_falls_in_dark_rect`
    cannot walk the page's operators. It reads the content stream as raw
    bytes via regex rather than parsing operators, so it tolerates a wider
    range of malformed structure -- at the cost of the false positives on
    black figure elements that item #16 exists to fix. That trade is
    intentional here: a page this heuristic wrongly flags gets a human
    second look; a page a broken parser silently called "clean" does not.
    """
    contents = page.get_contents()
    if contents is None:
        return False
    data = contents.get_data()
    if not (_DARK_FILL_RE.search(data) and _FILLED_RECT_RE.search(data)):
        return False
    return bool((page.extract_text() or "").strip())


def _redaction_findings(reader: PdfReader) -> tuple[list[Finding], list[str]]:
    suspect: list[int] = []
    warnings: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            if pdf_content.text_falls_in_dark_rect(page):
                suspect.append(index)
            continue
        except _UNPARSEABLE_CONTENT_ERRORS:
            pass  # fall through to the coarse heuristic below
        try:
            if _coarse_redaction_heuristic(page):
                suspect.append(index)
        except _UNPARSEABLE_CONTENT_ERRORS:
            warnings.append(
                f"page {index}: could not check for a failed redaction (the "
                "content stream would not parse); verify this page manually."
            )
    findings: list[Finding] = []
    if suspect:
        findings.append(
            Finding(
                category="possible-redaction",
                severity="high",
                summary=f"Possible unsafe redaction on {len(suspect)} page(s)",
                detail=(
                    "A dark filled rectangle sits directly over extractable text. "
                    "If a box was drawn over text to hide it, the text is still "
                    "selectable underneath. Verify by selecting over the black "
                    "area. This is a heuristic (approximate text geometry) and "
                    "can still flag ordinary design elements in rare cases."
                ),
                location=f"page {suspect[0]}",
                count=len(suspect),
                removable=False,
            )
        )
    return findings, warnings


def _javascript_present(reader: PdfReader) -> bool:
    try:
        names = reader.root_object.get("/Names")
        if names and "/JavaScript" in names:
            return True
        return "/OpenAction" in reader.root_object
    except (PdfReadError, AttributeError, TypeError, ValueError):
        return False


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report what a PDF carries, without modifying it."""
    reader = _read(path)
    findings: list[Finding] = _info_findings(reader)
    warnings: list[str] = []

    try:
        if reader.xmp_metadata is not None:
            findings.append(
                Finding(
                    category="xmp",
                    severity="medium",
                    summary="XMP metadata packet",
                    detail="An XMP packet duplicates and extends /Info, often adding "
                    "the authoring tool, edit history and document identifiers that "
                    "persist across saves.",
                    location="/Metadata",
                )
            )
    except (PdfReadError, ValueError, AttributeError):
        warnings.append("XMP metadata could not be parsed; it may still be present.")

    try:
        attachments = reader.attachments
    except (PdfReadError, AttributeError, ValueError):
        attachments = {}
    if attachments:
        findings.append(
            Finding(
                category="attachment",
                severity="high",
                summary=f"{len(attachments)} embedded file attachment(s)",
                detail="Whole files can be embedded in a PDF and travel with it "
                f"unnoticed: {', '.join(list(attachments)[:3])}.",
                location="/EmbeddedFiles",
                count=len(attachments),
            )
        )

    if _javascript_present(reader):
        findings.append(
            Finding(
                category="javascript",
                severity="medium",
                summary="Document-level JavaScript or an open action",
                detail="Scripts stored in the PDF run when it is opened in some "
                "viewers; they can also carry authoring information.",
                location="/Names/JavaScript",
            )
        )

    annots, pages = _annotation_counts(reader)
    if annots:
        findings.append(
            Finding(
                category="annotations",
                severity="medium",
                summary=f"{annots} review annotation(s) on {pages} page(s)",
                detail="Comments, highlights and sticky notes carry their authors' "
                "names and the text of the review.",
                location="/Annots",
                count=annots,
            )
        )

    revisions = _incremental_updates(path)
    if revisions:
        findings.append(
            Finding(
                category="incremental-history",
                severity="high",
                summary=f"{revisions} prior revision(s) retained",
                detail=(
                    "PDFs are saved by appending, so earlier versions of the document "
                    "-- including content since edited out -- remain recoverable in "
                    "the file. Sanitizing rewrites it and drops them."
                ),
                location="file trailer",
                count=revisions,
            )
        )

    findings.extend(_cropbox_findings(reader))
    redaction_findings, redaction_warnings = _redaction_findings(reader)
    findings.extend(redaction_findings)
    warnings.extend(redaction_warnings)
    return findings, warnings


def sanitize(src: Path, dest: Path, **_options: object) -> tuple[list[Finding], list[str]]:
    """Write a metadata-stripped copy of ``src`` to ``dest``.

    Removes /Info, XMP, embedded attachments, document JavaScript and markup
    annotations, and drops incremental-update history by rewriting the file.
    Content-level leaks (possible redaction, CropBox) are carried into
    ``warnings`` -- they are NOT fixed.
    """
    found, warnings = inspect(src)
    reader = _read(src)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    removed = [f for f in found if f.removable]
    unfixable = [f for f in found if not f.removable]

    # Drop /Info entirely. NOT add_metadata({}) -- that MERGES into the
    # dictionary cloned from the reader, so every original field survives.
    writer.metadata = None
    try:
        writer.xmp_metadata = None
    except (AttributeError, ValueError):  # pragma: no cover - version guard
        warnings.append("XMP packet could not be cleared automatically.")

    root = writer.root_object
    names = root.get("/Names")
    if names is not None and "/JavaScript" in names:
        del names["/JavaScript"]
    if "/OpenAction" in root:
        del root["/OpenAction"]
    if names is not None and "/EmbeddedFiles" in names:
        del names["/EmbeddedFiles"]

    for page in writer.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        kept = []
        for ref in annots:
            try:
                if ref.get_object().get("/Subtype") not in _MARKUP_ANNOTS:
                    kept.append(ref)
            except (PdfReadError, AttributeError, TypeError, ValueError):
                kept.append(ref)
        if kept:
            page[NameObject("/Annots")] = ArrayObject(kept)
        else:
            del page["/Annots"]

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        writer.write(handle)

    for finding in unfixable:
        warnings.append(f"NOT FIXED -- {finding.summary}: {finding.detail}")
    return removed, warnings
