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
- `latextify/citations/plaintext.py` — in-text marker linkage and typed
  reference-list stripping.

### QA methodology: screenshot the render

Text-only inspection of `body.tex` hides layout bugs. Render the output PDF to
PNGs (PyMuPDF, `uv run python <scratchpad>/render_pdf.py`) and eyeball it after
each fix — this is how the figure overflow and duplicate reference list were
caught and verified. Compare against the published YIG PDFs in `~/Downloads`.

### Dependency map

- The remaining items (2–4) are independent polish; each touches one stage.

---

## Tier 2 — Medium Impact

2. **Strip MathML leaking into `.bib` titles** — Crossref sometimes returns a
   title containing raw `<mml:math>…</mml:math>` (the `klingler2018spintorque`
   entry's "YIG/Co" arrived this way); it should be stripped or converted to
   plain/LaTeX math before landing in `references.bib`.
   - [ ] Detect + strip/convert MathML in reconciled Crossref titles (tests on
     a title carrying an `<mml:math>` block)

## Tier 3 — Nice-to-Have

3. **Re-associate a stray "Table N:" caption paragraph** — when the caption is
   typed as a separate paragraph after the table (not Word's Caption style) it
   lands as body text instead of the float's `\caption{}`. Figure captions
   already get this sibling-paragraph search; tables do not yet.

4. **Drop the trailing `\textbf{\hfill\break}` artifact** — an empty
   bold/line-break paragraph survives at the end of the YIG body (from a blank
   styled paragraph in the source). Harmless but untidy.

## Completed

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
