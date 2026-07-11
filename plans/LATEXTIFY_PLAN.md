# LaTeXtify — Word Manuscript to Journal-Ready LaTeX/PDF

Convert scientific manuscripts from .docx to LaTeX projects formatted for a
chosen journal (APS/AIP REVTeX, Elsevier, Nature/Springer, IEEE), with
citation extraction to BibTeX, journal-appropriate citation styles,
hyperlinked references, figure handling (embedded or user-supplied vector
files), and PDF compilation via Tectonic. CLI-first; core is a library so a
GUI can wrap it later. Each item below carries a model assignment and
self-contained executor context so a cheaper model can run it standalone.

**Status:** Active
**Created:** 2026-07-11
**Updated:** 2026-07-11

---

## Context

### How the pieces fit together

Python package `latextify` (uv-managed, Python 3.11+). The skeleton exists
(item 1, done) — every subpackage's `__init__.py` docstring describes its
planned modules and contracts; read it before implementing an item there.

```
latextify/
├── ingest/      # docx open, preflight validation, pandoc invocation, media extraction
├── model/       # intermediate representation — frozen dataclasses only, no I/O
├── citations/   # per-source extractors (zotero, mendeley, endnote, word-native,
│                # plaintext), Crossref client, .bib emitter, reconciliation
├── figures/     # override resolution (folder convention + manifest), vector conversion
├── templates/   # registry loader + journals/<name>/ data folders
│                # (manifest.yaml, Jinja2 templates, vendored class files)
├── emit/        # output-project writer; per-journal metadata macro mapping
├── compile/     # Tectonic wrapper, log parsing
├── report/      # consolidated per-run conversion report
└── cli.py       # thin typer CLI over the library
tests/
└── fixtures/    # small .docx corpus, one fixture per exercised feature
```

Conversion body goes through pandoc (docx → JSON AST → panflute filters →
LaTeX body). Citations are extracted directly from the docx XML field codes,
NOT from pandoc output, because field codes carry full CSL JSON that pandoc
lossily simplifies. The two streams rejoin in the emitter.

### Data / control flow

```
paper.docx ──> [preflight] ──> findings ─────────────────────────┐
    │                                                             │
    ├──> [pandoc AST + filters] ──> IR body ──┐                   │
    │                                         │                   v
    ├──> [citation extractors] ──> refs.bib + confidence ──> [report.md]
    │                                         │                   ^
figures/ + figures.yaml ──> [override resolve]│                   │
paper.yaml (metadata) ────────────────┐       │                   │
templates/journals/<j>/manifest.yaml > [emitter] ──> output tree ─┘
                                              │
                          output/<journal>/   v
                            main.tex          (user-owned; generated ONCE, never overwritten)
                            generated/*.tex   (body, preamble, metadata — regenerated each run)
                            figures/, references.bib
                                              │
                                    [tectonic] ──> paper.pdf
```

### Model routing

Assignments use the Agent tool's `model` parameter (haiku / sonnet / opus).
The orchestrating session dispatches one item per agent with the prompt:
*"Read plans/LATEXTIFY_PLAN.md — the Context section and item N only — then
implement item N following the Executor protocol."*

| Model | Model ID | Used for | Items |
|---|---|---|---|
| Haiku 4.5 | claude-haiku-4-5-20251001 | Mechanical, pattern-following work with a worked example to copy | 10, 16, 18, 20, 22 |
| Sonnet 5 | claude-sonnet-5 | Standard implementation: parsers, wrappers, emitters, well-specified heuristics | 2, 3, 5, 6, 8, 9, 11, 12, 13, 15, 17, 19, 21, 23 |
| Opus 4.8 | claude-opus-4-8 | Design-heavy or make-or-break: abstraction-proving, fiddly binary/XML formats, probabilistic matching | 4, 7, 14 |

Routing rationale: item 4 fixes the registry schema every later journal
copies (get it wrong once, pay four times); item 7 is the flagship feature
and Word's complex-field encoding is genuinely fiddly; item 14 is
open-ended heuristic design. Items 10/22 are Haiku *because* items 4 and
10-12 leave worked examples to imitate. Everything else is well-specified
by its context block.

### Executor protocol (read this before implementing any item)

1. Read this Context section and your item's block. The target subpackage's
   `__init__.py` docstring lists the planned modules — follow those names.
2. Branch: `git checkout -b feat/<item-slug>` from `main`. Never commit to
   `main` directly.
3. Environment: `uv sync` (first run downloads pandoc via pypandoc-binary).
   Add new runtime deps to `pyproject.toml` `[project.dependencies]`.
4. IR types go in `latextify/model/` as frozen dataclasses — never return
   ad-hoc dicts across stage boundaries.
5. Write tests alongside the code (`tests/test_<area>.py`); fixtures go in
   `tests/fixtures/` (see its README for naming). `uv run pytest` and
   `uv run ruff check .` must pass before you finish.
6. Close out per plan-hygiene: strike the item into `## Completed` with date
   and outcome, update the header `**Updated:**` date, same commit.
7. Merge: `git checkout main`, `git merge feat/<slug> --no-edit`, delete the
   branch.
8. If you discover the plan is wrong (missing dependency, wrong assumption),
   STOP and report rather than improvising a different architecture.

### Resolved decisions

- (2026-07-11) **Citation sources:** mixed/unknown — support Zotero/Mendeley
  field codes (CSL JSON), EndNote, Word-native, and plain-text reconstruction
  via Crossref, with graceful degradation and a confidence-scored
  reconciliation report.
- (2026-07-11) **Journal families:** REVTeX (APS/AIP), elsarticle (Elsevier),
  sn-jnl/svjour3 (Nature/Springer), IEEEtran (IEEE). REVTeX first.
- (2026-07-11) **Interface:** CLI first; GUI later wraps the library.
- (2026-07-11) **TeX engine:** Tectonic, auto-managed (no MiKTeX/TeX Live
  dependency; cross-platform Windows/macOS).
- (2026-07-11) **Re-run model:** split generated/manual — `main.tex` is
  user-owned and written once; regenerated content lives in `generated/`.
- (2026-07-11) **Figure overrides:** folder convention (`figures/fig1.pdf`
  matched by number) by default, optional YAML manifest for ambiguous cases.
- (2026-07-11) **Metadata:** best-guess parse from docx into `paper.yaml` on
  first run; sidecar is source of truth thereafter.
- (2026-07-11) **Scope contract:** inputs are *manuscripts* using Word styles
  (styled headings, equation-editor math, inline figures with captions).
  Preflight reports unsupported constructs instead of silently mangling
  them. Quality bar: "compiles cleanly under the journal class + punch-list
  report", not camera-ready.
- (2026-07-11) **Execution model:** per-item model routing + executor
  protocol so cheaper models run items standalone from this document.

### Known risks

- Pandoc fidelity ceiling: OMML equations convert well; complex tables and
  floating objects do not. Preflight (item 2) is the mitigation.
- Tectonic vs journal classes: revtex4-2 VERIFIED present in the Tectonic
  bundle (2026-07-11, item 6) — compiles on demand, no vendoring needed for
  REVTeX. sn-jnl (item 12) still expected absent; the vendoring path
  (`templates/journals/<name>/vendor/` staged into the compile dir) is
  implemented and tested, ready for it.
- Crossref matching for hand-typed references is probabilistic; the
  reconciliation report (item 14) must make low-confidence matches loud.
- pypandoc-binary pins a pandoc version; record it in the report so
  conversions are reproducible.

### Dependency map

- TIER 1 COMPLETE (items 1-9 + 24, 2026-07-11): end-to-end
  docx→LaTeX→PDF works for revtex4-2 with linked field-coded citations
- Items 10-12 (journal templates) and 13-14 (more citation sources, which
  reuse item 24's sentinel linkage path) are all unblocked and
  parallelizable; 15 needs 9; 16 needs 2/6/7/9; 17 needs 3; 18 needs 4+7
- Item 5 requires items 3 + 4
- Item 9 requires item 3 (media extraction)
- Items 10–12 require items 4 + 5 (registry + emitter proven on REVTeX)
- Items 13–14 require item 7 (fields walker + bib infrastructure)
- Item 15 requires item 9; item 17 requires item 3
- Item 16 aggregates outputs of items 2, 6, 7, 9 — do after those
- Item 18 requires items 4 + 7

---

## Tier 1 — High Impact

## Tier 2 — Medium Impact

10. **Elsevier template** — elsarticle journal folder
    **Model:** Haiku 4.5 (copy the revtex4-2 folder as the worked example) · **Depends on:** 4, 5 · **Touches:** `latextify/templates/journals/elsarticle/`
    **Context:** `\documentclass[review]{elsarticle}`; authors as
    `\author[a]{Name}` + `\affiliation[a]{organization={...}, city={...},
    country={...}}`; corresponding author via `\cortext`; bib modes:
    numeric `elsarticle-num`, authoryear `elsarticle-harv`. Same golden-file
    test pattern as item 4.
    **Done when:** loader validates it; golden-file test passes; two-author
    fixture compiles under Tectonic (vendor elsarticle.cls if the bundle
    lacks it).

12. **Nature/Springer template** — sn-jnl journal folder
    **Model:** Sonnet 5 · **Depends on:** 4, 5 · **Touches:** `latextify/templates/journals/sn-jnl/`
    **Context:** Springer Nature `sn-jnl.cls` (options like `pdflatex,sn-nature`);
    almost certainly NOT in the Tectonic bundle — vendor the cls + sn-*.bst
    into `vendor/` (check Springer's LaTeX kit license permits
    redistribution; if not, download-on-first-use with cached copy and a
    manifest `vendor_fetch` URL). `\author*[1]{}` marks corresponding;
    `\affil[1]{}`.
    **Done when:** golden-file test passes; fixture compiles via the
    vendoring path specifically.

13. **EndNote + Word-native citation extractors**
    **Model:** Sonnet 5 · **Depends on:** 7 · **Touches:** `latextify/citations/endnote.py`, `wordnative.py`
    **Context:** Reuse item 7's fields.py walker. EndNote: instruction
    `ADDIN EN.CITE` followed by XML `<EndNote><Cite><record>...` (fields:
    `<titles><title>`, `<contributors><authors>`, `<dates><year>`,
    `<electronic-resource-num>` = DOI). Word-native: `customXml/item*.xml`
    or `word/bibliography.xml`-referenced `b:` namespace `b:Source` elements
    keyed by `b:Tag`, matched to `w:sdt` citation content controls in the
    body. Both map into the same RefEntry → bib.py path.
    **Done when:** one fixture per source yields correct .bib entries and
    resolved cites; unknown-field-code case degrades to a report warning,
    not a crash.

14. **Plain-text citation reconstruction** — the mixed-collaborator safety net
    **Model:** Opus 4.8 (open-ended heuristics + confidence design) · **Depends on:** 7 · **Touches:** `latextify/citations/plaintext.py`, `crossref.py`, `reconcile.py`
    **Context:** Trigger when no field codes found. Detect in-text markers:
    `[12]`, `[3-5,8]`, `(Smith et al., 2020)`, superscript run numerals.
    Segment the typed reference list (numbered/indented paragraphs after a
    "References"/"Bibliography" heading). Per reference: query Crossref
    `GET https://api.crossref.org/works?query.bibliographic=<text>&rows=3`
    (set a mailto User-Agent; respect rate limits). Score candidates:
    rapidfuzz title similarity + year match + first-author surname match;
    accept ≥ threshold, else emit RefEntry from the raw string with a
    `verify` flag. Reconciliation report lists every reference with source,
    score, and DOI-or-flag.
    **Done when:** `hand_cited.docx` fixture (≥10 refs) reconstructs ≥80%
    with correct DOIs (mock Crossref in tests; one optional live-marked
    test); every below-threshold ref appears flagged in the report; numeric
    marker ranges expand correctly.

15. **Figure manifest + vector conversion**
    **Model:** Sonnet 5 · **Depends on:** 9 · **Touches:** `latextify/figures/convert.py`, `override.py`
    **Context:** `figures.yaml` schema: `{<figure-number>: <path>}`, beats
    folder convention on conflict. SVG must become PDF for LaTeX inclusion:
    try cairosvg first; if cairo DLLs are unavailable on Windows, fall back
    to svglib+reportlab and note fidelity limits in the report line. EPS:
    pass through (Tectonic/xelatex handles via repstopdf?) — TEST this; if
    not, convert via ghostscript when present, else report an actionable
    error. PDF/PNG/JPG pass through.
    **Done when:** manifest beats folder convention in a conflict test;
    an SVG override lands as PDF in the output tree; EPS behavior is tested
    and documented, whichever path wins.

16. **Consolidated conversion report** — report.md per run
    **Model:** Haiku 4.5 · **Depends on:** 2, 6, 7, 9 · **Touches:** `latextify/report/`
    **Context:** Every stage already produces finding/record dataclasses
    (see `report/__init__.py` for the section list). This item is
    aggregation + deterministic markdown rendering (stable ordering so
    diffs are meaningful) + exit-code policy: nonzero when any
    error-severity finding or compile error exists.
    **Done when:** a full-pipeline fixture run emits report.md with all four
    sections populated; ordering is stable across runs; exit codes tested.

17. **Table normalization** — Word tables → booktabs LaTeX
    **Model:** Sonnet 5 · **Depends on:** 3 · **Touches:** `latextify/ingest/filters.py`
    **Context:** panflute Table nodes → booktabs (`\toprule`/`\midrule`/
    `\bottomrule`, no vertical rules); infer column alignment from cell
    content (numeric → right/S column). Merged cells → `\multicolumn`;
    tables with row spans or nesting get a preflight-style warning and a
    verbatim-ish fallback rather than silent corruption.
    **Done when:** a tables fixture converts to compiling booktabs output;
    a pathological merged-cell table produces the warning path.

18. **Citation style switching polish** — numeric ↔ author-year toggle
    **Model:** Haiku 4.5 · **Depends on:** 4, 7 · **Touches:** `latextify/cli.py`, manifests
    **Context:** `--citation-style numeric|authoryear` CLI flag; validate
    against the journal manifest's `bib.modes` (error listing allowed modes
    if unsupported — IEEE has no authoryear); selected mode changes bibstyle
    + natbib options in the rendered preamble.
    **Done when:** flag round-trips into the preamble for a journal with
    both modes; unsupported combination errors clearly; test per path.

## Tier 3 — Nice-to-Have

19. **GUI wrapper** — drag-and-drop, journal picker, PDF preview (FastAPI+Vue or Tauri, reusing thin_film_toolkit patterns). **Model:** Sonnet 5.

20. **Batch mode** — convert a folder of manuscripts, per-file reports, summary table. **Model:** Haiku 4.5.

21. **Supplementary material handling** — second .docx → SI document with S-prefixed numbering. **Model:** Sonnet 5.

22. **Additional journals** — ACS (achemso), IOP (iopart), Wiley — pure journal folders copying items 10-12 patterns. **Model:** Haiku 4.5.

23. **Equation audit tooling** — side-by-side render comparison of Word equation vs converted LaTeX for equation-heavy papers. **Model:** Sonnet 5.

## Completed

- ~~**#11 IEEE template**~~ (2026-07-11) — ieeetran journal folder (numeric-
  only bib mode, figure/figure* envs), `group_globally_by_affiliation()` in
  authors.py registered as `group_authors_global` Jinja global, golden-file
  + non-consecutive-grouping tests, real Tectonic compile of the rendered
  project. IEEEtran.cls IS in the Tectonic bundle — no vendoring. 11 tests.
- ~~**#24 Citation anchor planting via docx preprocessing**~~ (2026-07-11) —
  `plant_citation_sentinels()` rewrites citation field RESULTS to
  `ZZLTXCITE<i>ZZ` sentinels in a temp docx (shares fields.py's walker, so
  sentinel i == Citation.index i, nested fields proven aligned); emitter
  resolves sentinels to `\cite{...}` with comment+EmitWarning degradation;
  ingest docstring claims corrected. End-to-end test now asserts real
  `\cite{}` in body + all keys in the compiled .bbl. 180 tests, 0 skipped.
  NOTES: fldSimple sentinels must be sibling runs (pandoc drops fldSimple
  inner content); Tectonic needs `--keep-intermediates` to retain .bbl.
- ~~**#5 Project emitter**~~ (2026-07-11) — `emit_project()` public API,
  write-once main.tex + regenerated generated/{preamble,metadata,body}.tex,
  figure copy + anchor resolution with graceful degradation (EmitWarning,
  never a crash), `latextify convert` CLI command; unskipped both
  integration stubs; 175 tests total, 0 skipped, real end-to-end
  docx→PDF compiles pass. TWO FINDINGS: (1) `doi` package conflicts with
  revtex4-2's built-in `\doi` — removed from manifest; apsrev4-2.bst emits
  `\href{https://doi.org/...}` natively so hyperref suffices. (2) pandoc
  3.9 never emits Cite nodes from citation field codes → in-text `\cite`
  linkage gap → spawned item 24.
- ~~**#9 Figures: extraction + folder override**~~ (2026-07-11) — Figure/
  FigureSource IR, `extract_figures()` (pandoc Figure.caption when populated,
  adjacent-sibling regex fallback otherwise — item 3's empty-caption finding
  could NOT be reproduced on pandoc 3.9, both paths covered), folder-
  convention `resolve_overrides()` with pdf>eps>svg>png>jpg priority,
  `describe_source()` report lines; figures.docx fixture (3 caption styles);
  14 tests. File copying + anchor/caption swallowing deferred to item 5;
  manifest tier deferred to item 15 as planned.
- ~~**#3 Pandoc body pipeline**~~ (2026-07-11) — pypandoc docx→JSON AST→
  panflute filters (heading normalize+clamp to 3 levels, junk strip,
  RawInline `%%FIGURE/%%CITE` anchors)→LaTeX; OMML math verified surviving
  round-trip; equations.docx fixture; 19 tests (+1 compile-harness test
  gated on tectonic PATH).
- ~~**#4 Template registry + REVTeX**~~ (2026-07-11) — manifest schema
  (class/class_options/packages/bib.modes-with-per-mode-bibstyle/
  metadata_scheme/figure_env/vendor), loader with named-field validation,
  Jinja \VAR{}/%% LaTeX-safe delimiters, `group_authors` Jinja global,
  revtex4-2 folder as the clone template for items 10-12; golden-file
  tests; 18 tests. NOTE: `bib.modes.<mode>.bibstyle` (per-mode bst)
  supersedes the single-`style` sketch — required by elsarticle.
- ~~**#7 Zotero/Mendeley citation extraction**~~ (2026-07-11) — complex-field
  walker (split-run concat, nesting, fldSimple), CSL JSON→RefEntry parsers,
  CSL→BibTeX with ASCII-folded stable keys + a/b/c collisions + brace
  protection, document-ordered Citation list for anchor pairing, cross-doc
  dedup (DOI→id→fingerprint); hand-crafted OOXML fixture; 46 tests. Through-
  compile stub skipped pending item 5.
- ~~**#2 Docx ingest + preflight**~~ (2026-07-11) — lxml walker over
  document.xml/styles.xml; five detectors (text boxes, tracked changes,
  floating objects, SmartArt, equation-as-image) + style inventory;
  `run_preflight()` → PreflightReport; unsupported.docx + clean.docx
  fixtures with committed generator scripts; 14 tests.
- ~~**#8 Metadata sidecar**~~ (2026-07-11) — title/author/affiliation/
  abstract/keyword heuristics (superscript markers → affiliation indices),
  paper.yaml emission with # CHECK: low-confidence comments, named-field
  schema validation, write-once behavior; 21 tests. IR unification with
  item 4's canonical Meta happens at item 4 merge.
- ~~**#6 Tectonic compile wrapper**~~ (2026-07-11) — binary detection/download
  + platformdirs cache, `tectonic -X compile` invocation, vendored-file
  staging, log parser (structured diagnostics, terse + classic TeX formats).
  DE-RISK FINDING: revtex4-2 IS in the Tectonic bundle — real PRB-style doc
  compiled to PDF on Windows; vendoring fallback proven via planted
  missing-class test. 26 new tests.
- ~~**#1 Repo scaffolding**~~ (2026-07-11) — uv pyproject (deps + dev group,
  ruff, pytest), package skeleton with 8 subpackages whose `__init__.py`
  docstrings carry per-module context for executors, CLI stub with console
  script, tests/fixtures corpus README, smoke test. Remaining fixture .docx
  files are created by the items that exercise them.
