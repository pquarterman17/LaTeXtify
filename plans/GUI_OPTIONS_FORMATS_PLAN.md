# LaTeXtify — GUI Options Redesign + Input Formats

Successor to the (near-done) multi-file GUI plan: make the Options section
self-explanatory and context-aware (grouped clusters, hints everywhere,
input-aware toggles, per-document layout controls, journal-tracking citation
defaults), and widen what the pipeline accepts (more manuscript, bibliography,
and figure input formats). Frontend-first: round one restructures and
clarifies the page, round two adds the per-document emission and new intake
paths behind it.

**Status:** Active — Tiers 1 + 2 complete; only Tier 3 (external-converter
gate + CLI parity) remains
**Created:** 2026-07-18
**Updated:** 2026-07-18

---

## Context

### How the pieces fit together

- `latextify/gui/static/index.html` — buildless page, size-pinned at 906
  lines and effectively full (905). The redesign cannot fit; the ratchet's
  answer is to split it into `index.html` + `app.js` + `style.css` (each
  under the 500-line frontend ceiling; the index pin then graduates and is
  deleted). This split is the enabler for every other frontend item.
- `latextify/gui/server.py` — serves `/` with secret + demo-banner injection;
  gains a route for the split static assets. Size-pinned at 1021: additions
  must be offset in the same change.
- `latextify/templates/` manifests + `loader.py` — gain a per-journal
  *default* citation mode (today only the supported-modes set exists).
- `latextify/gui/server.py::/api/convert-multi` + `emit/project.py` — round
  two threads per-document layout fields (columns, line numbers, double
  spacing) through the form into emission; `supplement_onecolumn` is the
  existing precedent for a per-document layout flag.
- `latextify/ingest/pandoc.py` — pandoc natively reads `.odt`/`.rtf`/`.md`;
  new manuscript types are mostly routing + preflight + accept-list work.
- `latextify/citations/bibtex_in.py` — precedent for reference intake; new
  parsers land beside it (CSL-JSON, EndNote XML, PubMed `.nbib`).
- `latextify/figures/convert.py` — conversion fallback-chain pattern
  (cairosvg → svglib) to extend for EMF/WMF via an external converter.

### Data / control flow

dropzone → per-file role table (+ round 2: per-file layout mini-panels)
→ JS FormData → `/api/convert-multi` → emit (per-doc options) → compile
→ artifacts. Options panel holds only global settings (journal, citations,
outputs, online checks); per-document settings live on the file rows.

### Dependency map

- Item 1 (page split) blocks items 2–5 (all frontend).
- Items 2–5 are then independent of each other.
- Item 6 (per-doc emission) depends on item 1; items 7–10 are independent
  of each other and of the frontend items.
- Items 11–12 share the external-converter owner gate.

### Resolved decisions (2026-07-18)

- Options layout: **grouped clusters** (Conversion / Outputs / Online
  checks) with a one-line hint per option plus hover `title` explanations
  (equation audit explicitly called out as unclear today).
- Reactivity: **input-aware toggles only** — supplement-dependent options
  disabled until a supplement file exists; exclude-figures warns when figure
  files are staged. Journal auto-suggest and localStorage persistence were
  explicitly declined.
- Per-document options appear as **per-file mini-panels** on the uploaded
  file rows, not as global toggles. The earlier "draft/review mode" and
  "reprint vs preprint" ideas are absorbed here (columns choice + line
  numbers + double spacing per document); anonymize and figures-at-end stay
  global.
- Citation style **defaults to the journal's house style and tracks journal
  changes**; picking a non-default style asks a one-time inline confirm.
- Input formats wanted: manuscripts `.odt`/`.rtf`/`.md`; bibliographies
  CSL-JSON, EndNote XML, `.nbib`; figures EMF/WMF. (`.doc` needs LibreOffice
  — Tier 3 with its own gate.)
- Sequencing: **frontend split + UX first** (round 1), pipeline work second;
  plan lives in tracked `plans/` (this file).
- Override confirm (item 4) is an **inline confirm row** under the dropdown
  (⚠ "<journal>'s standard is <default> — use <choice> anyway? Confirm /
  Revert"), with conversion blocked until resolved; no browser dialogs.
  Journal change resets to the new journal's default and clears the state.
- Option explanations are **hover-only tooltips on every option** (panel
  stays compact); no permanently visible hint lines.
- Default citation mode is **numeric for all three multi-mode journals**
  (elsarticle, sn-jnl, wiley); single-mode journals default to their mode.

### Direction backlog (owner priorities, 2026-07-18 — NOT booked here)

Fidelity: cross-reference linking, siunitx units, mhchem → candidates for
MANUSCRIPT_FIDELITY_PLAN. Workflow: Open-in-Overleaf, arXiv package export,
LaTeX→Word (already deferred in FORMATS_AND_PRIVACY_PLAN). Journal growth:
generic fallback template, Nature/Science, Optica/SPIE, MDPI/Frontiers.
Book these into their plans when scheduled; listed here so the priorities
recorded in the 2026-07-18 discussion have exactly one home.

### Owner gates

- **Demo redeploy** is manual (Render dashboard) — click after round 1
  lands, and per round thereafter.
- **External converter dependency** (LibreOffice/Inkscape class) needs
  owner sign-off before items 11–12 start.

---

## Tier 1 — High Impact (round 1: frontend split + UX)

*(all shipped — see Completed)*

## Tier 2 — Medium Impact (round 2: per-document emission + intake)

*(all shipped — see Completed)*

## Tier 3 — Nice-to-Have (external-converter gate)

11. **EMF/WMF figure conversion** — Word's native vector format, via a
    detected external converter with the cairosvg→svglib fallback pattern

12. **`.doc` manuscripts** — LibreOffice-headless pre-conversion to docx as
    an optional, auto-detected dependency with an actionable error otherwise

13. **CLI parity for submission options** — expose `--layout` / line numbers
    / double spacing / `--anonymize` / `--figures-at-end` on `latextify
    convert` (deferred from item 6, which shipped GUI-first; cli.py sits at
    its ratchet pin and needs an offsetting extraction first)

---

## Completed

- ~~**#9 Manuscript inputs .odt / .rtf / .md**~~ (2026-07-18) — new
  `ingest/formats.py` routes pandoc's input format by extension; the five
  docx-specific stages degrade honestly (empty preflight, minimal Meta with
  sidecar round-trip, plain-text citation path via pandoc AST, generalized
  figure extraction that now also works for odt/md). Fixed two latent bugs:
  convert-multi renamed every upload to main.docx (breaking non-docx
  routing), and markdown's external images lacked --resource-path. Verified
  end-to-end: .md → compiled REVTeX PDF via the CLI.
- ~~**#10 Bibliography inputs CSL-JSON / EndNote XML / .nbib**~~ (2026-07-18)
  — new single dispatch point `citations/refs_import.py::parse_references_file`
  routes by extension to `csl_json_in.py` / `endnote_xml_in.py` (hardened
  lxml, XXE-tested) / `nbib_in.py`; accept lists widened (server + app.js +
  CLI help). Also fixed latent bug: `.ris` uploads previously fell through to
  the BibTeX parser and silently matched nothing.
- ~~**#6 Per-file layout mini-panels + emission**~~ (2026-07-18) — new
  `emit/submission.py`: DocumentLayout applied to rendered preambles, keyed
  by document class (REVTeX one/two-column = preprint/reprint + native
  `linenumbers`; other classes use standard options + lineno/setspace).
  Main/Supplement rows grow mini-panels (columns, line numbers, double
  spacing); the global "One-column supplement" toggle was absorbed by the
  supplement mini-panel (`supplement_columns=one` = the plain-article path;
  the `supplement_onecolumn` API field remains for compatibility).
- ~~**#7 Double-blind anonymize**~~ (2026-07-18) — placeholder author block,
  affiliations dropped, acknowledgments section/environment stripped from the
  body; paper.yaml sidecar keeps the real authors; noted in report warnings.
- ~~**#8 Figures at end**~~ (2026-07-18) — `endfloat` (nolists,tablesfirst)
  appended to both documents' preambles behind a global toggle.
- ~~**#4 Citation default + confirm**~~ (2026-07-18) — manifests already
  required ``bib.default_mode`` (all currently numeric); it is now exposed in
  ``GET /api/journals`` and the dropdown follows it on journal change. A
  non-default pick shows the inline ⚠ confirm row and blocks Preview until
  confirmed (held per journal) or reverted. app.js stayed under the ceiling
  by extracting the Export panel to ``export.js`` (90 lines).
- ~~**#2 Grouped Options layout**~~ (2026-07-18) — three fieldset clusters
  (Conversion / Outputs / Online checks), hover tooltip on every option
  (hover-only per resolved decision; the unclear equation-audit toggle now
  explains itself).
- ~~**#3 Input-aware toggles**~~ (2026-07-18) — combine/one-column disabled
  (and unchecked) without a Supplement file; exclude-figures shows an inline
  warning over staged figure files. Also fixed a latent bug: opt-nofigs
  changes now invalidate a stale preview like every other toggle.
- ~~**#5 Advertise accepted formats**~~ (2026-07-18) — dropzone text and the
  file-picker filter are generated from the same accept lists role detection
  uses.
- ~~**#1 Split the buildless page**~~ (2026-07-18) — index.html 905 → 133
  lines plus style.css (158) / app.js (450) / review.js (193), all under the
  500 ceiling; served via a `/static` mount; export helpers extracted to
  `gui/exporting.py` to offset server.py; the frontend pin graduated and was
  deleted. Review panel bridges via window.LTXApp / window.LTXReview.
- ~~**Citation-style labels + Crossref-email tooltip**~~ (2026-07-18) —
  pre-plan quick fix, commit 32b8081: dropdown shows example-labeled styles,
  disabled with explanation when the journal allows only one; email field
  explains the Crossref polite pool.
