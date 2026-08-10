# LaTeXtify — Broad Metadata Stripping & Inspection

Generalize the single-format `.docx` sanitizer (FORMATS_AND_PRIVACY item #3)
into a format-dispatched privacy engine covering PowerPoint, Excel, PDF,
images, and the legacy OLE2 binaries — plus a non-destructive **inspect** mode
that reports what a file carries without rewriting it. Successor to
FORMATS_AND_PRIVACY_PLAN's privacy half; that plan keeps only its deferred
reverse-to-Word note.

**Status:** Active
**Created:** 2026-08-10
**Updated:** 2026-08-10

---

## Context

### The deliverable

Two artifacts leave the app, and both matter:

1. **A cleaned file** the user sends to a collaborator, journal, or preprint
   server — same visible content, no authoring trail.
2. **An inspection report** naming what a file carries. This is what makes (1)
   *trustworthy*, and it is the only honest deliverable for formats we cannot
   safely rewrite (legacy binaries, PDF content-level leaks).

A strip with no way to verify it is mechanism, not product.

### How the pieces fit together

`.docx`/`.pptx`/`.xlsx` are all **OPC packages** — the same ZIP layout with
`docProps/*`, `[Content_Types].xml`, and `_rels/`. The hard-won part of
`ingest/docx_clean.py` is not the WordprocessingML scrub; it is dropping
archive members while keeping content-types and every `.rels` consistent, so
Office does not refuse to open the result. That machinery is format-agnostic
and becomes `privacy/opc.py`, shared by all three.

PDF and images need no new dependency: `pypdf` (already present for
`--combine-supplement`) reads `/Info` + XMP; `Pillow` (already present for
TIFF→PNG) handles EXIF. Only the legacy OLE2 path adds `olefile`.

```
privacy/registry.py   ← THE single dispatch point (ext → handler)
   ├── opc.py         shared OPC: docProps, content-types, rels consistency
   │     ├── ingest/docx_clean.py   (existing, refactored onto opc.py)
   │     ├── pptx.py                notes, hidden slides, embedded workbooks
   │     └── xlsx.py                hidden sheets, pivot caches, external links
   ├── pdf.py         /Info + XMP strip; DETECT redaction/CropBox/attachments
   ├── images.py      EXIF/GPS/serial/thumbnail strip
   └── ole.py         legacy .doc/.ppt: inspect + best-effort stream strip
```

Per the dual-registration rule, `registry.py` is the **only** place a format is
registered; CLI and GUI accept-lists derive from it rather than restating it.

### Resolved decisions (2026-08-10, via scoping Q&A)

- **Formats:** `.pptx`, `.xlsx`, PDF, images, plus legacy `.doc`/`.ppt`.
  `.docx` already exists and gets its known gaps closed.
- **PDF depth:** metadata strip + **detect-and-warn** for content-level leaks.
  Destructive flattening/rasterization was declined — it needs `pikepdf`
  (qpdf-backed) and can degrade the document, conflicting with the
  2026-07-18 dependency-light decision.
- **Inspect mode:** yes — a non-destructive report command, and the only
  honest deliverable for legacy binaries.
- **Surface:** CLI **and** GUI panel, mirroring the existing `clean-docx`
  shape. GUI additions must be offset against the server.py / app.js pins.
- **Legacy `.doc`/`.ppt` scope:** inspect fully; strip the OLE2 property
  streams best-effort; **warn explicitly** that fast-save fragments can retain
  deleted text and that Save-As-modern-then-clean is the reliable path. This
  is distinct from GUI_OPTIONS item #12 (converting `.doc` for *ingest*, which
  needs LibreOffice and stays parked) — stripping needs no converter.

### Dependency map

- Item 1 (report model) blocks everything — it is the shared vocabulary.
- Item 2 (`opc.py` extraction) blocks items 4 and 5; it is a refactor of a
  well-tested module, so it lands with the existing docx tests green and no
  behaviour change.
- Items 6, 7, 8 (PDF, images, OLE) are independent of the OPC line and of
  each other.
- Item 3 (registry) needs at least one handler; wire it early, extend per item.
- Items 9, 10 (CLI, GUI) come last — they only surface the registry.

---

## Tier 1 — High Impact

1. **Unified report model** — `privacy/report.py`: a `Finding` (severity,
   category, human explanation, where it was found) plus `InspectReport` and a
   generalized `CleanReport`. Replaces the docx-only `CleanReport` dataclass.
   - [ ] Findings carry *why it matters*, not just a field name
   - [ ] Existing docx `CleanReport` fields preserved for API compatibility

2. **Extract shared OPC machinery** — `privacy/opc.py` from
   `ingest/docx_clean.py`: member-dropping rewrite, `[Content_Types].xml`
   Override pruning, `.rels` Relationship pruning, docProps + thumbnail set.
   - [ ] `docx_clean.py` refactored onto it, all existing tests green
   - [ ] `docx_clean.py` shrinks (it is 431 lines against a 500 ceiling)

3. **Format registry** — `privacy/registry.py`: `inspect_file()` /
   `sanitize_file()` dispatching on extension; single registration point.
   - [ ] Unknown extension raises an actionable error naming what is supported

4. **PowerPoint `.pptx`** — `privacy/pptx.py`
   - [ ] Speaker notes (`ppt/notesSlides/*`), optionally kept
   - [ ] Hidden slides (`p:sld @show="0"`)
   - [ ] **Embedded chart workbooks** (`ppt/embeddings/*.xlsx`) — full source
         data behind a three-bar chart
   - [ ] Off-canvas objects positioned outside the slide bounds
   - [ ] Comments + authors (`p:cmAuthorLst`, comment parts), docProps

5. **PDF metadata + leak detection** — `privacy/pdf.py`
   - [ ] Strip `/Info` and the XMP metadata stream
   - [ ] **Detect** unsafe redaction (text under filled rectangles)
   - [ ] **Detect** CropBox < MediaBox (the analogue of the Word `srcRect`
         leak already fixed in `figures/crop.py`)
   - [ ] Detect embedded attachments, JavaScript, annotations, and
         incremental-update history (prior revisions recoverable)

## Tier 2 — Medium Impact

6. **Images EXIF/GPS** — `privacy/images.py`
   - [ ] Strip GPS, camera serial, software, timestamps, embedded EXIF
         thumbnail (which can differ from the visible image)
   - [ ] Offer it on the normal conversion path, since LaTeXtify already
         ships figures into the output PDF

7. **Excel `.xlsx`** — `privacy/xlsx.py`
   - [ ] Hidden sheets, rows, and columns
   - [ ] **Pivot caches** — retain full source data after the sheet is deleted
   - [ ] Defined names, external links, comments/notes, docProps

8. **Legacy `.doc` / `.ppt`** — `privacy/ole.py`
   - [ ] Inspect OLE2 `SummaryInformation` / `DocumentSummaryInformation`
   - [ ] Best-effort strip of those streams
   - [ ] Explicit fast-save warning; recommend Save-As-modern-then-clean

9. **CLI** — `latextify inspect FILE` (new) and `latextify clean` widened
   beyond `.docx`, both driven by the registry.

10. **GUI panel** — inspect + clean for every supported format, offsetting the
    server.py / app.js size pins.

## Tier 3 — Nice-to-Have

11. **OpenDocument `.odt`/`.ods`/`.odp`** — `meta.xml` + settings; same ZIP
    approach as OPC but a different layout. Deferred until asked for.

12. **Batch mode** — clean or inspect a directory of files in one command.

---

## Owner gates

- **`olefile` dependency** for legacy `.doc`/`.ppt` (item 8). Pure-Python,
  BSD, no native code, offline-kit friendly — materially unlike the
  LibreOffice-class dependency declined on 2026-07-18. Flagged rather than
  assumed; item 8 is the only item that needs it.

---

## Completed

_(nothing yet)_
