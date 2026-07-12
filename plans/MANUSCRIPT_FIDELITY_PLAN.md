# LaTeXtify — Real-Manuscript Fidelity

Conversion-fidelity improvements surfaced by running the tool on real, messy
Word manuscripts (as opposed to the clean synthetic test fixtures). Real
submissions rarely use Word's semantic styles the way the fixtures do, so each
real paper tends to expose a distinct class of "the code didn't know what to
do" — this plan tracks those classes and their fixes.

**Status:** Active
**Created:** 2026-07-12
**Updated:** 2026-07-12

---

## Context

### How this campaign started

The archived build plan (`plans/archive/LATEXTIFY_PLAN.md`, Complete) took the
tool from empty repo to a working, published, CI-green converter using
synthetic fixtures. The first conversion of a *real* manuscript (a private
YIG/FM heterostructure paper, kept outside the repo) then surfaced six distinct fidelity
gaps in ~10 minutes — all now fixed (see `## Completed`). This plan is the home
for the remaining and any future real-paper findings.

### The recurring root cause

Real manuscripts type structure instead of styling it: titles as large-font
text (not Title style), section headings as bare ALL-CAPS or list-styled lines
(not Heading styles), abstract labels like `ABSTRACT:` (not a bare `Abstract`).
Fixes must therefore lean on content heuristics, not Word styles — and must
generalize to the *class* (all label variants, all heading shapes), never the
one observed instance. See the `generalize-fixes` memory.

### Where the relevant code lives

- `latextify/ingest/pandoc.py` + `filters.py` — docx → pandoc AST → LaTeX body;
  panflute filters are where body-structure heuristics belong.
- `latextify/ingest/metadata_guess.py` — title-page heuristics (title/authors/
  affiliations/abstract/keywords) and `front_matter_span`; carries a
  `_looks_like_section_heading` heuristic (the docx-`_Para` sibling of the
  body filter's `_section_heading_title`).
- `latextify/ingest/frontmatter.py` — strips the recognized title page.
- `latextify/emit/project.py` — figure emission (`_figure_block`,
  `_is_wide_figure`), where figure width/`figure*` selection lives.
- `latextify/figures/extract.py` — image ↔ number ↔ caption association;
  `_textbox_captions` reads floating text-box captions pandoc drops.
- `latextify/citations/plaintext.py` — in-text marker linkage and typed
  reference-list stripping (`strip_reference_section_to_eof`, shared with the
  field-code path).

### QA methodology: screenshot the render

Text-only inspection of `body.tex` hides layout bugs. Render the output PDF to
PNGs (PyMuPDF, `uv run python <scratchpad>/render_pdf.py`) and eyeball it after
each fix — this is how the figure overflow and duplicate reference list were
caught and verified. Compare against the published YIG PDFs in `~/Downloads`.

### Dependency map

- No open items remain; each gap was an independent class touching one stage.
  Future findings should be appended as new gaps and closed the same way.

---

_No open items — all known real-manuscript gaps are fixed. The next real paper
is the best way to surface the next class; render the output PDF (screenshot
loop above) to catch it._

## Completed

- ~~**Gap 22 — "Supplemental Fig. N" captions + preflight false alarms**~~ (2026-07-12) —
  the second manuscript's *supplement* labels its five figure captions "Supplemental Fig. N:",
  which gap 20's `^(?:Figure|Fig\.?)` label regex rejected (the "Supplemental"
  prefix blocks the match), so all SI captions were dropped. Broadened
  `_CAPTION_LABEL_RE` to accept an optional Supplemental/Supplementary prefix and
  an `S`-prefixed number ("Fig. S1"), and exposed `looks_like_figure_caption`.
  Also fixed the follow-on: preflight's `detect_text_boxes` flagged all ten SI
  caption boxes as "content will be dropped" — now that they're recovered, that's
  a false alarm, so it skips text boxes whose text reads as a figure caption
  (`ingest/preflight.py`). Screenshot-verified: FIG. S1–S5 render with captions
  and S-numbering; no spurious text-box errors.
- ~~**Gap 21 — duplicate reference list on the FIELD-CODE path**~~ (2026-07-12) —
  the second real manuscript uses Zotero/EndNote field codes
  AND left the plugin's formatted bibliography in the body; the emitter renders
  `\bibliography` from the extracted entries, so the body list was a duplicate.
  Gap 9 stripped the typed list only on the *plaintext* path; the field-code
  branch never did. Extracted the cut logic into
  `plaintext.strip_reference_section_to_eof(tex)` and called it on the
  field-code branch of `emit/project.py` (with a warning naming what was
  removed). Screenshot-verified on the second manuscript: one hyperlinked bibliography, not two.
- ~~**Gap 20 — figure captions authored as TEXT BOXES**~~ (2026-07-12) — the second manuscript's
  four `FIG. N:` captions float in Word text boxes (`w:txbxContent`), which
  pandoc drops, so every figure rendered caption-less. Added
  `figures/extract._textbox_captions(docx)` — reads `word/document.xml`
  directly, keys each `FIG. N`/`Figure N` text box by its label number (label
  stripped, first of the DrawingML+VML duplicate pair wins) — used as a fallback
  when the AST caption search comes up empty. Screenshot-verified: all four
  captions now render. Pure fallback: a bad/absent docx yields no captions,
  never an error.
- ~~**Gap 19 — raw (Crossref-unmatched) references mis-rendered**~~ (2026-07-12) —
  a raw entry's whole verbatim reference was emitted as a BibTeX `title` plus a
  separate `year`, so apsrev4-2 sentence-cased the author names ("L. J.
  Cornelissen" → "L. j. cornelissen", "Nature Physics" → "nature physics") and
  printed the year twice ("(2015). (2015)."). Now emitted as a single
  double-brace-protected `title` (BibTeX "already-cased" signal) with only the
  trailing year lifted into a `year` field. The year field also cures a stray
  "()" disambiguation marker: apsrev builds an author-less entry's label from
  the cite-key stem + year, and colliding stems rendered an empty "()"— the
  year fills that slot and usually breaks the collision. `_guess_surname` now
  skips leading initials so raw cite-keys are surname-based (every "B." author
  no longer collapses to `b20xx`). `latextify/citations/bib.py` +
  `reconcile.py`. Verified against apsrev4-2 under Tectonic (incl. a
  same-surname/same-year pair) and on the regenerated YIG PDF: 0 stray markers.
- ~~**Gap 16 — wide tables upscaled / inconsistent with narrow ones**~~ (2026-07-12) —
  the spanning `table*` was hard-scaled with `\resizebox{\textwidth}{!}`, which
  *upscales* any table narrower than the page (bigger fonts/rules), so Table II
  looked larger than Table I. Replaced with the shrink-only graphicx idiom
  `\resizebox{\ifdim\width>\linewidth\linewidth\else\width\fi}{!}` — a wide table
  is scaled down only if it would overflow, never up, so all tables render at a
  consistent scale. `latextify/ingest/filters.py` (`_wrap_table_float`).
- ~~**Gap 17 — huge inter-paragraph gap in a page column**~~ (2026-07-12) — same
  root as gap 16: `\resizebox` scales proportionally, so forcing a table to
  `\textwidth` also inflated its *height*; the oversized `table*` float starved
  the adjacent column, and REVTeX's `\flushbottom` (reprint default) distributed
  the slack as large inter-paragraph glue (the "huge space on page 8"). Fixed by
  the shrink-only bound in gap 16 — natural-size floats no longer over-consume
  vertical space. No separate code change.
- ~~**Gap 18 — transparent raster figures show halo/edge lines**~~ (2026-07-12) —
  a raster with an alpha channel (the manuscript's one RGBA PNG, Fig 2) has no
  defined backdrop in the PDF: xdvipdfmx renders its transparent pixels against
  nothing, leaving faint lines bordering the figure. `latextify/figures/convert.py`
  now flattens any alpha onto white (`Image.alpha_composite`) for both passthrough
  rasters and the TIFF→PNG path; opaque images are copied byte-for-byte, unreadable
  ones fall back to a plain copy. Generalizes to any transparent image, any format.
- ~~**Gap 7 — list-styled / bare section headings → `\section`**~~ (2026-07-12) —
  new `promote_pseudo_headings` panflute filter rewrites ALL-CAPS / roman- /
  arabic-numbered headings (bare bold paragraphs AND single-item ListParagraph
  enumerates) to `Header` nodes; a genuine content list is left untouched. YIG
  now emits 5 real `\section`s + acknowledgements (was 0). `latextify/ingest/filters.py`.
- ~~**Gap 8 — figures unbounded / overflowing the page**~~ (2026-07-12) —
  `_figure_block` emitted a bare `\includegraphics` (natural pixel size → five
  "Float too large for page" warnings, up to 701pt overflow). Every figure is
  now bounded to `\linewidth`; landscape composites (aspect ≥ 1.3, measured via
  Pillow) route to `figure*` to span both columns. `latextify/emit/project.py`.
- ~~**Gap 9 — duplicate reference list left in the body**~~ (2026-07-12) —
  `strip_reference_section` only cut a `\section{References}`; a bold/bare
  "References" (not promoted because Title-case) slipped through, duplicating
  the typed list with scrambled `\cite` numbers. Added a bold/bare heading-line
  fallback; combined with Gap 7, the YIG list is now stripped.
  `latextify/citations/plaintext.py`. (The user-reported "references as author
  names, not numbers".)
- ~~**Gap 10 — single-marker numeric ranges dropped**~~ (2026-07-12) — `[8-10]`
  reaches `expand_numeric_range` as `8--10` (pandoc en dash → `--`); the
  single-dash separator split it 3 ways and the marker was silently dropped.
  `_RANGE_SEP` now accepts a run of dashes; `[8-10]`/`[11-13]`/`[19-23]` resolve.
- ~~**Gap 11 — EndNote temporary citations rendered literally**~~ (2026-07-12) —
  `{Davies, 2004 #78}` (pandoc-escaped) now recognized, consecutive/duplicate
  runs collapsed, resolved via the author-year index — which is now also built
  from raw-text (Crossref-unmatched) entries by their leading surname. Unresolved
  markers warn and stay literal, never fabricated.
- ~~**Gap 12 — wide tables overflowing the page**~~ (2026-07-12) — a table with
  ≥ 4 columns overflowed a single revtex column; such tables now emit as a
  spanning `table*` hard-bounded to `\textwidth` via `\resizebox` (narrow tables
  unchanged, never upscaled). `latextify/ingest/filters.py`.
- ~~**Gap 13 — JATS/MathML in `.bib` titles**~~ (2026-07-12) — Crossref titles
  carrying `<mml:math>…` and JATS inline tags (`<i>`/`<sub>`/`<sup>`) now have
  markup stripped, entities decoded, and whitespace collapsed at the `_first`
  cleaning boundary (`klingler2018spintorque` "YIG/Co"). `latextify/citations/crossref.py`.
- ~~**Gap 14 — stray "Table N:" caption paragraph**~~ (2026-07-12) — a caption
  typed as a paragraph after the table (not Word's Caption style) is now moved
  into the table's `\caption{}` with the "Table N:" label stripped (revtex
  renumbers); mirrors the figure sibling-caption search.
  `latextify/ingest/filters.py` (`associate_table_captions`).
- ~~**Gap 15 — trailing blank-paragraph artifact**~~ (2026-07-12) — an empty
  styled paragraph (bold line break / stray non-breaking space) rendered as
  `\textbf{\hfill\break}` / a lone `~`; `strip_word_junk` now drops any
  paragraph holding only whitespace/breaks (guarding image/cite/math/raw/note
  content). `latextify/ingest/filters.py`.

## Verification note

The full YIG manuscript now converts and compiles clean (revtex4-2): 5 real
`\section`s, one numbered reference list, all figures bounded (3 spanning
`figure*`), both tables spanning at a consistent (unscaled) size with proper
captions, all in-text markers resolved, no MathML in the bibliography, no
trailing artifact, no oversized-float column gap, and no transparency halo on
the RGBA figure. The rendered PDF was screenshot-verified page by page (see the
QA methodology in Context). The user's local `~/Downloads/LaTeXtify-YIG-output/`
output was regenerated with the fixed code on 2026-07-12 (gaps 16–18 included).

- ~~**Gap 1 — `[N]`/`(N)`-prefixed reference lists**~~ (2026-07-11) — segmentation
  now recognizes bracket/paren numbering prefixes (incl. the no-space `[4]Author`
  form), strips them from Crossref queries and keys. `latextify/citations/plaintext.py`.
- ~~**Gap 2 — TIFF figures**~~ (2026-07-11) — Word-embedded `.tif`/`.tiff`
  auto-convert to PNG via Pillow (TeX can't `\includegraphics` TIFF); actionable
  warning on failure, never a raw TIFF in the tree. `latextify/figures/convert.py`.
- ~~**Gap 3 — marker false positives**~~ (2026-07-11) — leading-zero + Miller-index
  triads (`[001]`, `[110]`, `[111]`) and pre-body superscripts (author-line
  affiliation marks) no longer read as citation markers. `latextify/citations/plaintext.py`.
- ~~**Gap 4 — title page duplicated in body**~~ (2026-07-12) — `front_matter_span`
  + `frontmatter.strip_front_matter_from_docx` remove the title page (which the
  metadata template re-renders) from the body before pandoc; conservative gate
  (author markers OR abstract heading) so non-manuscript docs pass through
  byte-identical.
- ~~**Gap 5 — composite superscript markers**~~ (2026-07-12) — `1*` (no comma)
  no longer loses the affiliation digit; tokenizes any run into alnum groups
  (affiliations) + symbols (corresponding flags). `latextify/ingest/metadata_guess.py`.
- ~~**Gap 6 — labeled abstract + unstyled section headings**~~ (2026-07-12) —
  `ABSTRACT:` / `Abstract.` / `Abstract —` recognized as abstract headings
  (was empty in paper.yaml + unstripped), and abstract consumption terminates at
  a bare ALL-CAPS / roman-numbered section heading. `latextify/ingest/metadata_guess.py`.
