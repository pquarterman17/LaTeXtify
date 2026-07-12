# LaTeXtify — Multi-file GUI + offline .bib matching

Turn the single-file GUI into a multi-file intake: drop main text, a
supplement, figure files, and a reference `.bib` at once; auto-detect each
file's role with a manual per-file override; expose the conversion options as
toggles. Backs onto a new capability — offline citation matching against a
user-supplied `.bib` (also the long-open offline-plan item 9).

**Status:** Active
**Created:** 2026-07-12
**Updated:** 2026-07-12 — items 1–4 shipped; only Tier 3 item 5 remains

---

## Context

### How the pieces fit together

- `latextify/gui/server.py` — FastAPI app; today one `/api/convert` endpoint
  takes a single docx. Gets a multi-file sibling.
- `latextify/gui/static/index.html` — buildless vanilla-JS SPA; gets the
  multi-file dropzone, per-file role dropdowns, and the options panel.
- `latextify/emit/project.py::emit_project` — the shared pipeline; gains a
  `references_bib_path` parameter threaded to the plaintext citation path.
- `latextify/citations/` — `reconcile.py` matches typed references (today vs
  Crossref); a new `bibtex_in.py` parses a `.bib` into `RefEntry`s, and a new
  matcher scores typed references against them (offline, before Crossref).
- `latextify/compile/pdf.py::staple_pdfs` — already built (`--combine-supplement`).
- Figure files: written as `figures/fig<N>.<ext>` beside the uploaded main
  docx so the existing folder-convention override (`figures/override.py`)
  picks them up. NB: overrides REPLACE embedded/placeholder figures — a docx
  with no embedded image for figure N has nothing to attach a dropped file to.

### Resolved decisions (2026-07-12, via GUI design Q&A)

- **Reference file** → build offline `.bib` matching (authoritative, no
  Crossref needed for matched refs). This closes offline-plan item 9.
- **Option toggles** → all four: combine-supplement, download project `.zip`,
  Crossref email + citation-style, equation-audit PDF. Compile-to-PDF and
  report stay on by default.
- **Role auto-detection**: `.docx` → main (or supplement if filename ~
  `supp|SI|supporting`); image/pdf/eps/svg → figure (+ number); `.bib/.ris`
  → references. Every file has a dropdown override (Main/Supplement/Figure #N/
  References/Ignore).

### Dependency map

- Item 1 (bib parser) → item 2 (matcher + emit/CLI wiring). Independent of GUI.
- Item 3 (GUI multi-file endpoint) needs item 2's `references_bib_path` +
  existing supplement/combine plumbing; also the zip + audit + streaming bits.
- Item 4 (frontend) needs item 3's endpoint contract fixed first.

---

## Tier 3 — Nice-to-Have

5. **Fully-separate figures** — insert a dropped figure file for figure N even
   when the docx has only a caption (no embedded placeholder). Backend gap
   noted in Context; out of scope unless a real manuscript needs it.

## Completed

- ~~**#1 BibTeX input parser**~~ (2026-07-12) — `latextify/citations/bibtex_in.py`:
  `parse_bibtex(text) → list[RefEntry]`; brace/paren/quote delimiters,
  two-pass `@string` macro resolution, case-protection stripping, DOI-URL
  normalization, `@preamble/@comment/@string` skipped, graceful on malformed
  entries. 20 unit tests (`tests/test_citations_bibtex_in.py`).
- ~~**#2 Offline .bib matching + wiring**~~ (2026-07-12) —
  `latextify/citations/bibmatch.py` scores typed refs against parsed `.bib`
  entries via the shared `reconcile.score_fields` blend; `reconcile_references`
  gains a `bib_entries` param (matched before Crossref, `source="bibfile"`,
  fully offline when the `.bib` covers the list). Threaded through
  `reconstruct_citations` → `emit_project(references_bib_path=...)` (main +
  supplement) and a CLI `--references lib.bib` flag. 11 tests
  (`tests/test_citations_bibmatch.py`). Closes offline-plan item 9.
- ~~**#3 GUI multi-file endpoint**~~ (2026-07-12) — `POST /api/convert-multi`
  in `latextify/gui/server.py`: main + optional supplement + figures[] (+
  `figure_numbers`) + optional `.bib` + options (combine, citation_style,
  crossref_mailto, equation_audit, want_zip, pdf). Uploads streamed to disk in
  1 MiB chunks with a 250 MB/file cap (`_stream_upload`; also retrofitted onto
  `/api/convert`, fixing its `await file.read()`). Figures land as
  `figures/fig<N>.<ext>` for the folder-convention override. Mints opaque
  tokens for main/supplement/combined/audit PDFs (`/api/pdf/{token}`) and the
  project `.zip` (new `/api/zip/{token}`). 9 tests incl. a tectonic end-to-end
  (pdf+combine+audit+zip) in `tests/test_gui.py`.
- ~~**#4 Frontend multi-file UI**~~ (2026-07-12) — rebuilt
  `latextify/gui/static/index.html` (buildless vanilla JS): multi-file
  dropzone, a per-file row with an auto-detected role dropdown
  (Main/Supplement/Figure/References/Ignore) + figure-number field, an options
  panel (Compile PDF / Combine supplement / Download .zip / Equation-audit
  checkboxes, Crossref email, citation-style select), and result actions —
  a PDF preview with Main/Supplement/Combined/Audit tabs plus .zip/combined/
  audit download links. Posts one `multipart/form-data` to
  `/api/convert-multi`. DOM-contract smoke test in `tests/test_gui.py`.
