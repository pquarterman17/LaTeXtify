"""Legacy OLE2 binaries (.doc, .ppt, .xls): inspection, and why not stripping.

The pre-2007 Office formats are not ZIPs. They are OLE2 Compound File Binary
containers -- a FAT-like filesystem inside one file -- whose authoring
metadata lives in two property-set streams, ``\\x05SummaryInformation`` and
``\\x05DocumentSummaryInformation``. Reading those is straightforward and this
module does it.

**Sanitizing them is deliberately refused, and that is the feature.**

Legacy Word and PowerPoint default to *fast save* (incremental save): rather
than rewriting the document, they append each change to the end of the file.
The consequence is that a ``.doc`` routinely contains text the author deleted
-- sometimes whole earlier drafts -- as recoverable fragments scattered
through the container. This is a documented property of the format and the
cause of some of the best-known document leaks on record.

No amount of property-stream editing fixes that. A tool that stripped the
SummaryInformation stream and reported "cleaned" would hand back a file that
looks sanitized, is not, and is now trusted. Refusing, and naming the one
remedy that actually works -- open it and Save As the modern format, whose
rewrite drops the fragments, then clean that -- is the only outcome we can
stand behind.

``olefile`` is an optional dependency (``pip install latextify[legacy]``): it
is needed only for this format family, so the default install stays as light
as the pandoc+Tectonic-only decision requires.
"""

from __future__ import annotations

from pathlib import Path

from .report import Finding

_INSTALL_HINT = (
    "Reading legacy .doc/.ppt/.xls metadata needs the 'olefile' package. "
    "Install it with:  pip install 'latextify[legacy]'"
)

#: OLE property-set fields worth naming, with why each matters.
_FIELDS = (
    ("author", "author", "high", "the document's original author"),
    ("last_saved_by", "last-modified-by", "high", "who last saved the file"),
    ("company", "company", "high", "the organization the Office copy was licensed to"),
    ("manager", "manager", "medium", "a manager name saved by Office"),
    ("title", "title", "low", "an internal working title"),
    ("subject", "subject", "low", "a subject line"),
    ("keywords", "keywords", "low", "keywords that can name an internal project"),
    ("comments", "description", "medium", "free-text notes saved with the file"),
    ("template", "template", "medium", "the template the file was built from"),
    ("last_printed", "timestamp", "low", "when the document was last printed"),
    ("create_time", "timestamp", "low", "when the document was created"),
    ("last_saved_time", "timestamp", "low", "when the document was last saved"),
)


def _load_olefile():  # type: ignore[no-untyped-def]
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - exercised via the hint test
        raise ValueError(_INSTALL_HINT) from exc
    return olefile


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="replace").rstrip("\x00").strip()
    return str(value).strip()


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report the OLE2 property-set metadata a legacy Office file carries."""
    olefile = _load_olefile()

    if not olefile.isOleFile(str(path)):
        raise ValueError(
            f"{path}: not a legacy OLE2 Office file. If this is actually a modern "
            "file with an old extension, rename it to .docx/.pptx/.xlsx."
        )

    findings: list[Finding] = []
    with olefile.OleFileIO(str(path)) as ole:
        meta = ole.get_metadata()
        for attr, category, severity, why in _FIELDS:
            value = getattr(meta, attr, None)
            if value in (None, "", 0):
                continue
            text = _decode(value)
            if not text or text == "0":
                continue
            findings.append(
                Finding(
                    category=category,
                    severity=severity,
                    summary=f"{attr.replace('_', ' ').capitalize()}: {text[:60]}",
                    detail=f"The OLE summary stream records {why}.",
                    location="\\x05SummaryInformation",
                    removable=False,
                )
            )

        edit_minutes = getattr(meta, "editing_time", None)
        if edit_minutes:
            findings.append(
                Finding(
                    category="editing-time",
                    severity="medium",
                    summary=f"Total editing time recorded: {edit_minutes}",
                    detail="Reveals how long the document was actually worked on.",
                    location="\\x05SummaryInformation",
                    removable=False,
                )
            )

        streams = ole.listdir()

    # Always present, always worth saying: this is the format's real risk.
    findings.append(
        Finding(
            category="fast-save-fragments",
            severity="high",
            summary="Legacy format may retain deleted text (fast save)",
            detail=(
                "Pre-2007 Office appends edits instead of rewriting the file, so "
                "deleted text -- sometimes entire earlier drafts -- can remain "
                "recoverable inside it. This cannot be removed by stripping "
                "metadata; only re-saving in a modern format rewrites the file."
            ),
            location=f"{len(streams)} OLE streams",
            removable=False,
        )
    )

    warnings = [
        "Legacy OLE2 files cannot be safely sanitized in place. Open this file, "
        "'Save As' the modern format (.docx/.pptx/.xlsx), then clean that copy -- "
        "the re-save rewrites the container and drops fast-save fragments."
    ]
    return findings, warnings


def sanitize(src: Path, dest: Path, **_options: object) -> tuple[list[Finding], list[str]]:
    """Refuse, with the remedy that actually works.

    See the module docstring: stripping the property streams would remove the
    visible metadata while leaving fast-save fragments of deleted text in
    place, producing a file that looks clean and is not.
    """
    modern = {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx"}.get(src.suffix.lower(), ".docx")
    raise ValueError(
        f"{src}: refusing to sanitize a legacy {src.suffix} file.\n"
        f"  Why: this format appends edits rather than rewriting, so deleted text can "
        f"remain recoverable in the file. Stripping its metadata would return a file "
        f"that looks clean but is not.\n"
        f"  Do this instead: open it, 'Save As' {modern}, then run clean on the "
        f"{modern} copy.\n"
        f"  To see what it currently exposes without changing it, run:  "
        f"latextify inspect {src}"
    )
