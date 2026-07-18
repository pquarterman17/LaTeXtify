# LaTeXtify — Alternate Formats & Manuscript Privacy

Features that extend LaTeXtify *beyond* the core `docx → journal LaTeX/PDF`
path: a text-only conversion toggle, a fix for a live image-privacy leak, a
metadata-stripped clean-`.docx` export, alternate HTML/Markdown output targets,
and (deferred) the reverse `→ Word` direction. Grouped here because none fit the
existing plans (offline-distribution, real-manuscript fidelity, the near-done
multi-file GUI) and they share one theme: what LaTeXtify can *output* and how
safely.

**Status:** Active
**Created:** 2026-07-13
**Updated:** 2026-07-18 — Tier 1 + items 3, 4, 5, 7 shipped; only #6
(reverse→Word, deferred) remains

---

## Context

### How the pieces fit together

The pipeline pivots on a single pandoc AST:
`docx → pandoc JSON → panflute filters → LaTeX` (`latextify/ingest/pandoc.py`),
with LaTeXtify's own stages hanging off it — metadata guess, figure
association, citation reconciliation (`→ references.bib`), journal templating.
Each item below touches a specific stage:

- **Exclude figures** (item 1) — `latextify/emit/project.py`: figure anchors
  (`%%FIGURE:N%%`) are resolved in one place (`_resolve_figure_anchors`); an
  exclude path strips them instead of emitting `\includegraphics`.
- **Image-crop leak** (item 2) — `latextify/figures/extract.py` +
  `convert.py`: Word crops images for display via `a:srcRect` but keeps the
  **full original pixels** in `word/media/`; pandoc extracts that original and
  nothing applies the crop, so hidden regions ship into `figures/figN.*` and the
  PDF. The fix reads `srcRect` and crops with Pillow at extract/convert time.
- **Clean-`.docx` export** (item 3) — a new pre-ingest module operating on the
  docx zip directly (as `figures/extract._textbox_captions` already does): strip
  `docProps/*`, accept-then-remove tracked changes, delete comments, scrub
  `settings.xml` rsids, drop hidden text.
- **HTML / Markdown export** (items 4, 5) — a new `latextify/emit/` sibling that
  reuses the filtered AST + metadata + figures + reconciled citations but swaps
  the final pandoc writer (`to="html"` / `to="markdown"`).
- **Reverse → Word** (item 6) — deferred; a brand-new opposite-direction pivot
  reusing none of the above machinery.

### Data / control flow (unchanged core; new branches marked ▸)

```
docx ─pandoc─> AST ─filters─> LaTeX ─> journal project ─tectonic─> PDF   (core)
  │                    ├─▸ pandoc(to=html)     ─> self-contained .html   (item 4)
  │                    └─▸ pandoc(to=markdown) ─> .md                     (item 5)
  ├─▸ srcRect crop applied to word/media before figure extraction         (item 2)
  └─▸ sanitize zip ─> clean .docx artifact                                (item 3)
```

### Resolved decisions (2026-07-13, via scoping Q&A)

- **Exclude Figures**: implement now — `emit_project` param + `--exclude-figures`
  CLI flag + GUI checkbox, default OFF. Strips figure floats entirely (no
  orphan captions). Applies to the supplement too, for consistency.
- **Privacy scope**: fix the crop leak in the LaTeX pipeline (item 2) AND emit a
  metadata-only clean `.docx` (item 3). **No** in-`.docx` image re-rasterization
  (the heavier "full Document Inspector" variant was declined).
- **Export targets**: HTML **and** plain Markdown. **Jupyter/.ipynb was
  considered and dropped** — a converted manuscript has no executable cells, so
  ipynb would only be a worse HTML.
- **Equation handling** (the open worry when this was scoped): HTML uses
  **MathML** (native in modern browsers → works in a self-contained offline
  page); Markdown keeps math as literal LaTeX `$...$` (renders on GitHub /
  pandoc viewers, raw source elsewhere). This is the standard pandoc behavior.
- **HTML offline strategy** (2026-07-18, was an owner gate): resolved as
  fully self-contained/offline -- MathML only, no CDN MathJax fallback (the
  old-browser-coverage tradeoff was judged not worth the network dependency
  for an offline-distribution tool), plus `--standalone --embed-resources`
  and every figure inlined as a base64 `data:` URI.
- **Reverse `→ Word`**: deferred — recorded as a future note only, no committed
  approach. `LaTeX → Word` (pandoc, best-effort) is the tractable half;
  `PDF → Word` is a fundamentally harder layout-reconstruction problem and is
  explicitly not planned.

### Dependency map

- Items 1, 2, 3 are independent and can land in any order.
- Item 4 (HTML) builds the shared AST-based export path and its HTML citation
  linker; item 5 (Markdown) builds on item 4's export scaffolding.
- Item 6 is deferred and independent of everything.

---

_Tier 1 is complete — see `## Completed`._

## Tier 2 — Medium Impact

*(Items 3, 4, 5, 7 shipped — see Completed.)*

## Tier 3 — Nice-to-Have

6. **Reverse direction `→ Word`** (deferred, note only) — no committed approach.
   `LaTeX → Word` via pandoc is best-effort and support-heavy on real journal
   classes; `PDF → Word` is a separate, much harder layout-reconstruction
   problem and is out of scope unless a concrete need appears.

---

## Owner gates

_(none open)_

---

## Completed

- ~~**#7 Plaintext-citation fidelity on the HTML/MD export**~~ (2026-07-18) —
  fixed the duplicate reference list on the no-field-codes path via an
  AST-level strip: `emit/alt_formats.py::_strip_reconstructed_reference_section`
  drops the typed reference `Header`-to-EOF from the panflute `Doc` before the
  HTML/Markdown writer runs (writer-agnostic; avoids porting plaintext.py's
  LaTeX-text regexes), gated on `strip_typed_list` so it fires ONLY when a list
  was reconstructed. New public `citations/plaintext.py::is_reference_heading_text`
  reuses the same heading classification `segment_reference_list` uses. Verified:
  plaintext exports (bracket_cited) now carry exactly ONE reference list in both
  targets; field-coded (zotero) and no-citation (clean) paths unchanged. Warning
  revised accordingly. In-text markers are still left as plain `[N]` text
  (numerically aligned with the reconstructed list) — hyperlinking them was
  explicitly out of scope and is an optional cosmetic future nicety, not tracked.
  9 tests; full suite green.
- ~~**#4 HTML export** + **#5 Markdown export**~~ (2026-07-18) — self-contained
  single-file export reusing the docx→AST ingest via a safe refactor
  (`ingest/pandoc.py::convert_docx_to_ast`; `filters.py` byte-unchanged) and a
  portable-anchor mechanism (`ingest/portable_anchors.py`) that survives
  pandoc's HTML/MD writers where the LaTeX anchors don't. `emit/alt_formats.py`
  (+ `_render`): HTML = `--mathml --standalone --embed-resources` (native
  MathML, figures inlined as base64 `data:` URIs, no network refs); Markdown =
  literal `$...$`, figures to `<stem>_files/`. Field-coded citations → linked
  `<a href="#ref-N">` (verified: link targets match anchor ids) + reference
  list; plaintext-cited path warns and leaves a duplicate list (tracked as
  #7). Shipped via `latextify export DOCX --format html|markdown` (new
  `cli_export.py`), and the GUI `POST /api/export-format` + `GET /api/alt/{token}`
  (guarded + demo rate-limited; frontend `alt-export.js`). Verified end-to-end
  (CLI + GUI). `ExportResult` added to the model layer. To offset the GUI
  route, both single-upload routes were extracted to `gui/uploads_routes.py`
  (+ `gui/upload_utils.py`), dropping server.py 1010→921 and its pin to 921.
- ~~**#3 Metadata-stripped clean-`.docx` export**~~ (2026-07-18) — new
  `latextify/ingest/docx_clean.py::sanitize_docx` streams a sanitized archive
  copy: strips `docProps/{core,app,custom}.xml` **and the saved thumbnail**,
  accepts tracked changes (insertions kept, deletions dropped), deletes
  comments + markers, drops `w:vanish` hidden runs, scrubs `settings.xml`
  rsids and `people.xml`, keeping `[Content_Types].xml` + every `.rels`
  consistent with what it dropped. Uses the shared hardened XML parser
  (thread-safe). Exposed as `latextify clean SRC DEST` (new `cli_clean.py`)
  and `POST /api/clean-docx` + `GET /api/clean/{token}` (guarded + demo
  rate-limited); server.py offset by extracting session/token infra to
  `gui/downloads.py` (1019→1010). Verified end-to-end: cleaned download
  carries no docProps/thumbnail/people. Known gaps documented in the module
  (row/cell-level tracked changes, paragraph-mark merges, scattered rsid
  attrs). Does NOT re-rasterize cropped images (item 2 covers crops).
- ~~**#2 Fix the Word image-crop privacy/fidelity leak**~~ (2026-07-13) — Word
  crops images for display via `a:srcRect` but keeps the full original pixels,
  and nothing applied the crop, so hidden regions shipped into `figures/` and
  the PDF. New `latextify/figures/crop.py` reads each main-flow picture's
  `srcRect` from `word/document.xml` (thousandths-of-a-percent insets, negatives
  clamped, degenerate rejected), binds it to the right `Figure` by document
  order cross-checked against the media basename (unique-basename fallback; never
  a wrong crop), and applies it with Pillow at convert time; vector/PDF crops
  degrade to a warning. `CropRect` IR on `Figure`; guarded to the EMBEDDED source
  (an override is not cropped). 22 tests + a `cropped_figure.docx` fixture
  (4-quadrant image → 50×50 all-red after crop). To satisfy the size ratchet,
  extracted `emit/anchors.py` (anchor resolution out of `project.py`, pin
  1176→1000), `cli_batch.py` (batch command out of `cli.py`, pin 713→517), and
  grouped GUI option bindings; also closed the Exclude Figures merge's ratchet
  debt on `project.py`/`cli.py`/`index.html`.
- ~~**#1 Exclude Figures toggle**~~ (2026-07-13) — `emit_project(exclude_figures=)`
  strips both figure-anchor shapes (`_strip_figure_anchors`) and skips figure
  extraction/copy for the main document AND the supplement; prunes this
  document's prior images on toggle so an existing tree (and any `.zip` export)
  ships no images. `--exclude-figures` CLI flag on `convert`; GUI "Exclude
  figures (text only)" checkbox (`convert-multi` form field + `index.html`).
  Default off. 5 tests (drop-all, silent unmatched-anchor strip, prune-on-toggle,
  CLI, GUI). `latextify/emit/project.py`, `cli.py`, `gui/server.py`,
  `gui/static/index.html`; `tests/test_emit.py`, `test_cli.py`, `test_gui.py`.
