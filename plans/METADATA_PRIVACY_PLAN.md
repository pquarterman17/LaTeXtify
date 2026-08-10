# LaTeXtify — Broad Metadata Stripping & Inspection

Generalize the single-format `.docx` sanitizer (FORMATS_AND_PRIVACY item #3)
into a format-dispatched privacy engine covering PowerPoint, Excel, PDF,
images, and the legacy OLE2 binaries — plus a non-destructive **inspect** mode
that reports what a file carries without rewriting it. Successor to
FORMATS_AND_PRIVACY_PLAN's privacy half; that plan keeps only its deferred
reverse-to-Word note.

**Status:** Active
**Created:** 2026-08-10
**Updated:** 2026-08-10 — items 1-10 shipped; only Tier 3 (11-12) remains

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

_Tiers 1 and 2 are complete — see `## Completed`._

## Tier 3 — Nice-to-Have

11. **OpenDocument `.odt`/`.ods`/`.odp`** — `meta.xml` + settings; same ZIP
    approach as OPC but a different layout. Deferred until asked for.

12. **Batch mode** — clean or inspect a directory of files in one command.

---

## Owner gates

- **`olefile` dependency** — resolved by making it an optional `[legacy]`
  extra rather than a runtime dependency, so the default install is
  unchanged. Only `.doc`/`.ppt`/`.xls` inspection needs it.

---

## Completed

- ~~**#1 Unified report model**~~ (2026-08-10) — `privacy/report.py`:
  `Finding` (category/severity/summary/**detail**/location/count/**removable**),
  `InspectReport`, `SanitizeReport`. `detail` is mandatory prose explaining why
  a finding matters; `removable` separates what sanitizing fixes from what only
  a human can. The existing docx `CleanReport` was left untouched.
- ~~**#2 Shared OPC machinery**~~ (2026-08-10) — `privacy/opc.py`:
  `rewrite_package` drops members while pruning `[Content_Types].xml`
  Overrides and every `.rels` Relationship together, plus `docprops_findings`
  shared by all three OPC formats. `ingest/docx_clean.py` was deliberately NOT
  refactored onto it — rewriting a security-relevant, well-tested module to
  save duplication is a bad trade; `docx_adapter.py` wraps it instead.
  `archive_guard` gained a `label` param so a `.pptx` error stops saying
  `.docx`.
- ~~**#3 Format registry**~~ (2026-08-10) — `privacy/registry.py`, the single
  registration point; CLI and GUI accept lists derive from it. Refuses to
  sanitize a file onto itself.
- ~~**#4 PowerPoint**~~ (2026-08-10) — embedded chart workbooks (+ the chart's
  `c:externalData` reference), speaker notes, hidden slides (part + notes +
  `p:sldId` entry), comments/authors, docProps. Off-canvas shapes are detected
  and reported, never deleted.
- ~~**#5 PDF**~~ (2026-08-10) — strips `/Info` (via `writer.metadata = None`;
  `add_metadata({})` merges and silently kept everything), XMP, attachments,
  JavaScript, markup annotations, and drops incremental-update history by
  rewriting. Detects failed redaction and CropBox leaks as unfixable.
- ~~**#6 Images**~~ (2026-08-10) — GPS/serial/artist/software/thumbnail;
  rebuilds from pixel bytes so maker notes cannot survive. ICC profile kept by
  default so figure colours are unchanged.
- ~~**#7 Excel**~~ (2026-08-10) — pivot caches, external links, comments,
  docProps removed; hidden sheets/rows reported but NOT removed, because
  formulas and charts reference sheets by name.
- ~~**#8 Legacy `.doc`/`.ppt`/`.xls`**~~ (2026-08-10) — inspection via the
  optional `olefile` extra. Sanitizing is **refused** with the remedy that
  works (Save As modern, then clean): fast save can leave deleted text in the
  file, so a stripped `.doc` would look clean without being clean.
- ~~**#9 CLI**~~ (2026-08-10) — `latextify inspect FILE [-v]` (exit 1 on
  findings so it can gate a release script), `clean` widened to every format
  (`--keep-notes`, `--keep-color-profile`), and `formats`. `cli_clean.py`
  superseded and deleted.
- ~~**#10 GUI panel**~~ (2026-08-10) — `POST /api/inspect` (writes nothing,
  issues no token, deletes the upload in a `finally`) and `POST
  /api/clean-file` replace the docx-only `/api/clean-docx`. Both live in
  `gui/uploads_routes.py`, so `server.py` (916 against a 921 pin) was not
  touched. The panel renders unfixable findings distinctly and never counts
  them as removed.
- ~~**Fixtures + tests**~~ (2026-08-10) — `make_leaky_files.py` plants known
  leaks with a `.truth.json` sidecar. Running the real engine against them
  immediately found two defects: python-pptx writes an empty
  `<Company></Company>`, so `find()` returned the placeholder and masked the
  real value (`_text_of` now takes the first non-empty match), and pypdf's
  `add_metadata({})` merges rather than replaces. 27 privacy tests + 4 GUI
  tests; suite 1190 → 1217.
