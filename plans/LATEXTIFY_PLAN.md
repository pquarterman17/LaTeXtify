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

- TIERS 1 AND 2 COMPLETE (items 1-18 + 24, all 2026-07-11): four journal
  families compile (sn-jnl + elsarticle via vendoring), four citation
  sources extract AND link with style switching, figures with vector
  conversion, booktabs tables, per-run report.md, `latextify convert --pdf`
  end to end
- Remaining: Tier 3 only (items 19-23, 25 — GUI, batch, SI, more journals,
  equation audit, pathological-table compile gap)
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



## Tier 3 — Nice-to-Have


19. **GUI wrapper** — drag-and-drop, journal picker, PDF preview (FastAPI+Vue or Tauri, reusing thin_film_toolkit patterns). **Model:** Sonnet 5.

20. **Batch mode** — convert a folder of manuscripts, per-file reports, summary table. **Model:** Haiku 4.5.

21. **Supplementary material handling** — second .docx → SI document with S-prefixed numbering. **Model:** Sonnet 5.

22. **Additional journals** — ACS (achemso), IOP (iopart), Wiley — pure journal folders copying items 10-12 patterns. **Model:** Haiku 4.5.



## Completed

- ~~**#27 Affiliation marker/paragraph cross-order mismatch**~~ (2026-07-11)
  — author→affiliation linking now cross-validates marker VALUES in a
  three-tier strategy: (1) match against affiliation paragraphs' own
  leading markers (order-independent; unmatched markers dropped + CHECK,
  unreferenced labels CHECK-flagged), (2) numeric markers map
  positionally-by-value when paragraphs are unlabeled, (3) first-seen-order
  fallback now ALWAYS CHECK-flags non-ascending order. Out-of-range safety
  net intact; clean fixture byte-identical, zero flags. 5 new tests with
  yaml round-trips. 543 tests total.
- ~~**Bug-hunt wave, citations + ingest/CLI areas**~~ (2026-07-11) — 13 bugs
  fixed across two hunters (+ hunter B's 5 recorded under #26). Citations:
  dash-joined ranges `[1]–[3]` silently dropped middle refs (HIGH); Word
  auto-numbered (w:numPr) reference lists never linked (HIGH — the common
  real-world case); unbalanced literal braces corrupted the whole .bib
  (HIGH — escape_latex now emits self-balanced \textbraceleft{}); Crossref
  client crashed on non-200/timeout/bad JSON; fully-unidentifiable
  citations merged. Ingest/CLI: guessed paper.yaml could be written
  self-inconsistent, permanently breaking later runs (CRITICAL); corrupt/
  non-OOXML docx leaked raw tracebacks through three modules and the CLI
  (HIGH); corresponding-author email stolen from abstract text; email
  regex swallowed trailing periods; `equations --pdf` crashed raw on
  compile timeout. 47 regression/stress tests lock in the not-a-bug
  verdicts (CJK keys, 600-citation perf ~0.04s, suffix cascade past 'z',
  degenerate/huge docs, unicode paths). Follow-up fix landed same day:
  figure-in-table-cell desync — extract_figures now counts via the same
  doc.walk() traversal as plant_anchors (numbering cannot drift), in-table
  figures carry Figure.in_table and resolve to a bare \includegraphics
  (floats are illegal in table cells), proven by end-to-end compile +
  both-orders numbering tests. Remaining deferral: #27 only. 538 tests,
  0 xfail.
- ~~**#26 IEEEtran fails on citation-free manuscripts**~~ (2026-07-11) —
  fixed in the bug-hunt wave: bibliography inclusion moved to regenerated
  `generated/bibliography.tex` (\bibliography line when entries exist,
  self-explaining comment when none); new main.tex template \inputs it;
  pre-existing main.tex files preserved with a one-line-migration
  EmitWarning. Proven by real clean.docx→ieeetran→PDF compile. Same wave
  also fixed: metadata LaTeX-specials escaping at the render boundary (all
  four journals — compile-breaker for real titles), uniform MetadataError
  for out-of-range affiliation indices, EmitResult.warnings actually
  reaching report.md + markdown-injection sanitization, duplicate-figure-
  number silent overwrite. 481 tests total.
- ~~**#25 Pathological-table fallback doesn't compile**~~ (2026-07-11) —
  reproduced in all four journals (`longtable undefined`); macro-injection
  fix (a) evaluated empirically: works for elsarticle/sn-jnl/revtex4-2
  (REVTeX ships its own longtable shim) but NOT ieeetran (longtable is
  fundamentally incompatible with native two-column). Chose uniform degrade
  (b): pathological tables become best-effort booktabs with vMerge content
  duplicated into every spanned row (grid-expansion helper keeps column
  indices correct), nested tables flattened to joined text, bold "[table
  structure simplified -- verify against source]" note; zero new package
  exposure. Also fixed adjacent bug: body_result.findings were silently
  dropped from EmitResult.warnings/report. FINDING spawned item 26
  (IEEEtran + empty bibliography). 427 tests total.
- ~~**#23 Equation audit tooling**~~ (2026-07-11) — `latextify equations
  paper.docx [--pdf]`: raw OMML walk paired with pandoc's converted LaTeX
  per equation (index/kind/snippet), equations_audit.md + numbered
  audit.pdf; combined-compile-then-isolate strategy so one broken equation
  gets marked FAILED (verbatim) without killing the audit; count-mismatch
  flag for dropped/merged equations. FINDING: matrices/cases/eqArr all
  convert cleanly on pandoc 3.9 — the real hazard is blank abandoned
  equations, which now surface as "(empty)" instead of vanishing. 29 tests. —
  report/render.py (deterministic report.md: preflight, citations incl.
  ReconcileRecord confidence/verify flags, figure sources + conversion
  notes, compile diagnostics; empty sections say "None"), preflight wired
  into emit_project (EmitResult.report_path additive), CLI --pdf (compile
  via ensure_tectonic with journal.vendor staging — closes the items 10/12
  wiring gap) + --report/--no-report + exit-code policy. 18 tests incl.
  real --pdf CLI compile.
- ~~**#18 Citation style switching polish**~~ (2026-07-11) — audit found the
  items 4/5 architecture already re-run-switchable (\bibliographystyle lives
  in regenerated preamble.tex, main.tex write-once); added `latextify
  journals` command (lists journals + modes), clean CLI error surfacing for
  unsupported modes, and 10 tests incl. the re-run style-switch proof.
- ~~**#10 Elsevier template**~~ (2026-07-11) — elsarticle folder with BOTH
  bib modes (per-mode bibstyles elsarticle-num/-harv; natbib options must be
  CLASS options for this journal, folded into \documentclass by its own
  preamble template), canonical frontmatter env (abstract+keywords emitted
  from Meta IR), vendored elsarticle.cls v3.5 2026-01-09 from TeX Live tlnet
  (bundle's v3.3 has a fatal expl3 hook bug at \maketitle — vendored file
  shadows it, proven in the compile log). Both modes compile to real PDFs.
- ~~**#14 Plain-text citation reconstruction**~~ (2026-07-11) — plaintext.py
  (marker detection incl. brace-protected `{[}12{]}` forms + line-wrap-
  tolerant author-year regex, reference-list segmentation, range expansion,
  body linkage, duplicate reference-section stripping), crossref.py (mocked
  in tests, mailto configurable via CLI/env), reconcile.py (rapidfuzz
  scoring, threshold 0.72), ReconcileRecord/ReconciliationReport IR for
  item 16. Fixture: 11/12 refs reconstructed with DOIs, 1 flagged. 52 tests.
- ~~**#17 Table normalization**~~ (2026-07-11) — normalize_tables panflute
  filter (booktabs rules, alignment inference, \multicolumn for gridSpan;
  vMerge/nested tables left untouched + FilterFinding warning), tables.docx
  fixture, booktabs added to all four journal manifests + goldens, CRLF
  harness bug fixed (newline=""). FINDING spawned item 25: pandoc's default
  rendering of pathological tables can't compile in fragment mode.
- ~~**#12 Nature/Springer template**~~ (2026-07-11) — sn-jnl folder with
  vendored sn-jnl.cls + sn-mathphys-num/-ay.bst (LPPL 1.3 verified in file
  headers — redistribution OK, no vendor_fetch mechanism needed;
  .gitattributes guards vendor bytes from CRLF mangling), `format_affil_refs`
  Jinja global (per-author inline affiliation refs, `\author*` marks
  corresponding), citation style doubles as a CLASS option (bst basename ==
  class option, exploited in the preamble template), hyperref option clash
  avoided via \PassOptionsToPackage matching the emitter's line. VERIFIED
  absent from Tectonic bundle — first load-bearing use of vendoring; both
  bst modes proven via real BibTeX passes. 14 tests. FLAGGED: nothing wires
  `journal.vendor` into a compile CLI yet (goes with item 16's CLI work).
- ~~**#13 EndNote + Word-native citation extractors**~~ (2026-07-11) —
  endnote.py (EN.CITE XML incl. style-wrapped leaves + double HTML
  encoding), wordnative.py (CITATION field instructions resolved against
  customXml b:Source map); both flow through the shared fields.py walker
  and item 24's sentinels with ZERO sentinel changes — Word-native sdt
  citations wrap real CITATION fields, so mixed-manager document order
  falls out free (proven by interleaved 3-source test). Malformed data
  degrades to EmitWarning, never crashes. 47 tests incl. real compile.
- ~~**#15 Figure manifest + vector conversion**~~ (2026-07-11) —
  figures.yaml manifest tier (beats folder convention; named-field
  FigureManifestError validation), FigureSource.MANIFEST +
  Figure.conversion_note + EmitResult.figures (all additive), SVG→PDF
  (cairosvg → svglib/reportlab fallback; svglib is the working path on this
  machine, cairo DLL absent), EPS→Ghostscript-or-actionable-EmitWarning.
  VERIFIED: Tectonic rejects raw EPS includegraphics (real compile test).
  33 tests. Deps: svglib+reportlab required, cairosvg optional extra.
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
