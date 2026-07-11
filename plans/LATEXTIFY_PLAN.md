# LaTeXtify — Word Manuscript to Journal-Ready LaTeX/PDF

Convert scientific manuscripts from .docx to LaTeX projects formatted for a
chosen journal (APS/AIP REVTeX, Elsevier, Nature/Springer, IEEE), with
citation extraction to BibTeX, journal-appropriate citation styles,
hyperlinked references, figure handling (embedded or user-supplied vector
files), and PDF compilation via Tectonic. CLI-first; core is a library so a
GUI can wrap it later.

**Status:** Active
**Created:** 2026-07-11
**Updated:** 2026-07-11

---

## Context

### How the pieces fit together

Python package `latextify` (uv-managed, Python 3.11+), organized so each
stage is independently testable:

```
latextify/
├── ingest/      # docx open, preflight validation, pandoc invocation, media extraction
├── model/       # intermediate representation — frozen dataclasses
│                # (Document, Section, Figure, Table, Equation, Citation, RefEntry)
├── citations/   # per-source extractors (zotero, mendeley, endnote, word-native,
│                # plaintext), Crossref client, .bib emitter, reconciliation
├── figures/     # override resolution (folder convention + manifest), vector conversion
├── templates/   # registry loader; templates/<journal>/ = manifest.yaml + class
│                # files + Jinja2 body/preamble templates (journals are DATA, not code)
├── emit/        # project emitter — writes output tree, maps metadata to each
│                # journal's author/affiliation macro scheme
├── compile/     # Tectonic wrapper, log parsing
├── report/      # conversion report (preflight findings, citation confidence,
│                # figure overrides, compile warnings)
└── cli.py       # thin CLI over the library (latextify paper.docx --journal prb)
```

Conversion body goes through pandoc (docx → JSON AST → panflute filters →
LaTeX body). Citations are extracted directly from the docx XML field codes,
NOT from pandoc output, because field codes carry full CSL JSON that pandoc
may lossily simplify. The two streams rejoin in the emitter.

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
templates/<journal>/manifest.yaml ──> [emitter] ──> output tree ──┘
                                              │
                          output/<journal>/   v
                            main.tex          (user-owned; generated ONCE, never overwritten)
                            generated/*.tex   (body, preamble, metadata — regenerated each run)
                            figures/, references.bib
                                              │
                                    [tectonic] ──> paper.pdf
```

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
  user-owned and written once; regenerated content lives in `generated/`
  includes so manual polish survives re-conversion.
- (2026-07-11) **Figure overrides:** folder convention (`figures/fig1.pdf`
  matched by number) by default, optional YAML manifest for ambiguous cases.
- (2026-07-11) **Metadata:** best-guess parse from docx into `paper.yaml` on
  first run; sidecar is source of truth thereafter.
- (2026-07-11) **Scope contract:** inputs are *manuscripts* using Word styles
  (styled headings, equation-editor math, inline figures with captions).
  Preflight reports unsupported constructs (text boxes, SmartArt, tracked
  changes) instead of silently mangling them. Quality bar is "compiles
  cleanly under the journal class + punch-list report", not camera-ready.

### Known risks

- Pandoc fidelity ceiling: OMML equations convert well; complex tables and
  floating objects do not. Preflight (item 2) is the mitigation.
- Tectonic vs journal classes: revtex4-2 and sn-jnl must be verified against
  Tectonic's bundle early (item 6 sub-task) — fallback is vendoring class
  files into the output tree.
- Crossref matching for hand-typed references is probabilistic; the
  reconciliation report (item 14) must make low-confidence matches loud.

### Dependency map

- Item 1 first; then items 2, 3, 4, 6, 7, 8 are parallelizable
- Item 5 requires items 3 + 4
- Item 9 requires item 3 (media extraction)
- Items 10–12 require items 4 + 5 (registry + emitter proven on REVTeX)
- Items 13–14 require item 7 (bib infrastructure)
- Item 15 requires item 9; item 17 requires item 3
- Item 16 aggregates outputs of items 2, 6, 7, 9 — do after those
- Item 18 requires items 4 + 7

---

## Tier 1 — High Impact

1. **Repo scaffolding** — uv project, `latextify` package skeleton, ruff, pytest with fixture .docx corpus
   - [ ] pyproject.toml (uv), ruff config, pytest config
   - [ ] Package directories per Context layout, empty `__init__.py` chain
   - [ ] `tests/fixtures/` with 2–3 small real-ish manuscripts (Zotero-cited, hand-cited, equation-heavy)

2. **Docx ingest + preflight** — open the ZIP/XML, inventory the document, report what conversion can and cannot handle
   - [ ] Style inventory (headings, captions, body styles)
   - [ ] Unsupported-construct detection: text boxes, SmartArt, tracked changes, floating objects, image-based equations
   - [ ] Preflight findings feed the report module

3. **Pandoc body pipeline** — docx → pandoc JSON AST → panflute filters → LaTeX body into the IR
   - [ ] Spike: pandoc binary management (pypandoc-binary) and `--extract-media`
   - [ ] Filters: strip Word junk, normalize headings to `\section` levels, placeholder anchors for figures/citations
   - [ ] OMML equation conversion sanity tests against fixture corpus

4. **Template registry + REVTeX** — journals as data: `templates/<journal>/manifest.yaml` + class files + Jinja2 templates
   - [ ] Manifest schema: document class, options, packages, bibliography style, natbib/biblatex mode, author/affiliation macro mapping, figure env conventions
   - [ ] revtex4-2 (PRB-style) implementation as the schema-proving first journal
   - [ ] Registry loader with validation (bad manifest = clear error)

5. **Project emitter** — write the output tree with the generated/manual split
   - [ ] `main.tex` written only if absent (user-owned thereafter)
   - [ ] `generated/preamble.tex`, `generated/metadata.tex`, `generated/body.tex` regenerated every run
   - [ ] Metadata mapping layer: one `Author`/`Affiliation` IR → per-journal macro emission

6. **Tectonic compile wrapper** — auto-managed engine, parsed diagnostics
   - [ ] Tectonic install/detection story on Windows + macOS
   - [ ] Verify revtex4-2 compiles under Tectonic; vendor class files if the bundle lacks them
   - [ ] Log parser: surface errors/warnings into the report instead of raw TeX spew

7. **Zotero/Mendeley citation extraction** — field codes → CSL JSON → `references.bib`, `\cite` keys in body, hyperlinked DOIs
   - [ ] Field-code walker over document.xml (ADDIN ZOTERO_ITEM / Mendeley markers)
   - [ ] CSL JSON → BibTeX mapping with stable citation keys
   - [ ] hyperref + doi linking wired through the journal preamble

8. **Metadata sidecar** — `paper.yaml` extraction and override
   - [ ] Best-guess title/author/affiliation/abstract/keywords parse from the docx front matter
   - [ ] Write `paper.yaml` on first run only; validate + consume it on every run

9. **Figures: extraction + folder override** — embedded media out, better files in
   - [ ] Extract embedded images with figure-number and caption association
   - [ ] `figures/figN.*` override convention with per-figure report line (overridden vs embedded)
   - [ ] Caption detection from Word caption style or "Figure N:" text

## Tier 2 — Medium Impact

10. **Elsevier template** — elsarticle manifest + metadata mapping (`\author[a]` / `\affiliation` scheme)

11. **IEEE template** — IEEEtran manifest, two-column conventions, IEEE bib style

12. **Nature/Springer template** — sn-jnl class family; verify Tectonic compatibility, vendor if needed

13. **EndNote + Word-native citation extractors** — EndNote XML traveler records; Word `customXml` bibliography

14. **Plain-text citation reconstruction** — the mixed-collaborator safety net
    - [ ] Marker detection (`[12]`, `(Smith et al., 2020)`) and reference-list segmentation
    - [ ] Crossref `query.bibliographic` matching with confidence scores
    - [ ] Reconciliation report section: per-reference match, score, and "verify me" flags

15. **Figure manifest + vector conversion** — `figures.yaml` explicit mapping; SVG→PDF conversion (pick resvg/cairosvg on Windows), EPS passthrough

16. **Consolidated conversion report** — single `report.md` per run: preflight findings, citation confidences, figure overrides, compile diagnostics

17. **Table normalization** — Word tables → booktabs-style LaTeX with column-type inference

18. **Citation style switching polish** — numeric ↔ author-year toggle where the journal permits, driven by manifest options

## Tier 3 — Nice-to-Have

19. **GUI wrapper** — drag-and-drop, journal picker, PDF preview (FastAPI+Vue or Tauri, reusing thin_film_toolkit patterns)

20. **Batch mode** — convert a folder of manuscripts; per-file reports

21. **Supplementary material handling** — separate SI document with its own numbering

22. **Additional journals** — ACS (achemso), IOP (iopart), Wiley

23. **Equation audit tooling** — side-by-side render comparison of Word equation vs converted LaTeX for equation-heavy papers

## Completed

(none yet)
