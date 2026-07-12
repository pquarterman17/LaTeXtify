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
  affiliations/abstract/keywords) and `front_matter_span`; already carries a
  `_looks_like_section_heading` heuristic that the body filter can reuse.
- `latextify/ingest/frontmatter.py` — strips the recognized title page.

### Dependency map

- Item 1 is independent; it touches only the body-conversion filters.

---

## Tier 1 — High Impact

1. **Promote list-styled / bare section headings to `\section`** — the manuscript
   authors its section headings ("INTRODUCTION", "METHODS", ...) in Word's
   *ListParagraph* style with no Heading style, so pandoc reads them as list
   items: the YIG body converted with **0 `\section`s** and the headings became
   `\begin{enumerate}` items. Content is all present but the paper has no
   section structure.
   - [ ] A panflute filter (in `latextify/ingest/filters.py`) that detects a
     paragraph/list-item reading as a section heading — reuse/extend
     `metadata_guess._looks_like_section_heading` (short, ALL-CAPS or
     roman-numbered "I. Introduction", no terminal punctuation, standalone) —
     and rewrites it to a `Header` node at the right level.
   - [ ] Generalize across shapes: ALL-CAPS ("INTRODUCTION"), roman-numbered
     ("I. Introduction"), and numbered ("1. Introduction") headings; and across
     styles: ListParagraph, Normal, and any non-Heading style. Do NOT promote
     genuine list content (guard: heading-like line standalone in its own
     paragraph, not part of a multi-item list of sentences).
   - [ ] Tests beyond the YIG instance: a synthetic body with each heading shape
     converts to `\section`/`\subsection`; a real bulleted list stays a list
     (no false promotion); compiles under Tectonic.
   - [ ] Re-run the YIG paper: body shows the section structure, `\section`
     count > 0, no spurious `enumerate` wrapping the headings.

## Completed

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
