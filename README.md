# LaTeXtify

[![CI](https://github.com/pquarterman17/LaTeXtify/actions/workflows/ci.yml/badge.svg)](https://github.com/pquarterman17/LaTeXtify/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

Convert scientific manuscripts from Word (`.docx`) into journal-ready LaTeX
projects and PDFs. Pick a journal, point at a manuscript, get a compilable
LaTeX project with extracted citations, journal-appropriate formatting, and
a compiled PDF — **no TeX installation required** (the Tectonic engine is
downloaded and managed automatically).

```
latextify convert paper.docx --journal revtex4-2 --pdf
```

## What it does

- **Journal formatting** — emits a project using the journal's real document
  class with correct author/affiliation macros, compiled with Tectonic.
  Class files missing from Tectonic's bundle are vendored (where licenses
  permit) and staged automatically.
- **Citations with links** — extracts references from **Zotero/Mendeley**
  field codes, **EndNote**, **Word's built-in** citation manager, or
  **hand-typed text** (reconstructed via Crossref with confidence scoring),
  emits `references.bib` with DOIs, and links in-text citations to real
  `\cite{}` commands. Citation style (numeric ↔ author-year) switches per
  journal support.
- **Figures** — extracts embedded images, or swap in your own
  vector/high-res files by dropping `figures/fig1.pdf` beside the docx (or
  an explicit `figures.yaml`). SVG converts to PDF automatically.
- **Equations** — Word equation editor (OMML) math converts to LaTeX;
  `latextify equations` produces a side-by-side audit for equation-heavy
  papers.
- **Tables** — clean Word tables become booktabs; pathological merged-cell
  tables degrade to a compilable, clearly-marked simplification instead of
  silent corruption.
- **Honest by design** — every run writes `report.md`: preflight findings,
  per-reference citation confidence with "verify me" flags, figure
  provenance, compile diagnostics. The quality bar is *compiles cleanly +
  punch list*, not silent camera-ready claims.
- **Re-run safe** — `main.tex` is written once and never overwritten; your
  manual LaTeX polish survives re-conversion (regenerated content lives in
  `generated/`).

## Supported journals

| Template | Family | Citation modes | TeX class source |
|---|---|---|---|
| `revtex4-2` | APS / AIP (PRB, PRL, APL, ...) | numeric | Tectonic bundle |
| `elsarticle` | Elsevier | numeric, author-year | vendored (v3.5, LPPL) |
| `ieeetran` | IEEE | numeric | Tectonic bundle |
| `sn-jnl` | Nature / Springer | numeric, author-year | vendored (LPPL) |
| `achemso` | ACS | numeric | Tectonic bundle |
| `iopart` | IOP | numeric | vendored (LPPL) |
| `wiley` | Wiley (NJD) | numeric, author-year | user-supplied¹ |

¹ Wiley's class file is proprietary and cannot be redistributed; the
template works once you place `WileyNJD-v2.cls` in the output directory
(the error message tells you exactly what is missing).

Adding a journal is data, not code: a folder with a manifest and two Jinja
templates. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

Not yet on PyPI — install from source with [uv](https://docs.astral.sh/uv/):

```
git clone https://github.com/pquarterman17/LaTeXtify
cd LaTeXtify
uv sync
```

## Usage

```
# Convert + compile to PDF (report.md written alongside)
uv run latextify convert paper.docx --journal revtex4-2 --pdf

# Choose citation style where the journal supports both
uv run latextify convert paper.docx --journal elsarticle --citation-style authoryear

# Supplementary material: S-numbered second document sharing the bibliography
uv run latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf

# A folder of manuscripts at once (continue-on-error + summary)
uv run latextify batch drafts/ --journal revtex4-2 --pdf

# Equation conversion audit for equation-heavy papers
uv run latextify equations paper.docx --pdf

# Local web GUI (drag-and-drop; requires the gui extra: uv sync --extra gui)
uv run latextify gui

# List registered journals and their citation modes
uv run latextify journals
```

Output layout per conversion:

```
output/<journal>/
├── main.tex          # yours — written once, never overwritten on re-runs
├── generated/        # regenerated every run: preamble, metadata, body, bibliography
├── figures/          # resolved figure files (embedded or your overrides)
├── references.bib    # extracted bibliography with DOIs
└── report.md         # what happened, what to verify
```

On first conversion a `paper.yaml` sidecar is written beside your docx with
the guessed title/authors/affiliations — correct it once; it is the source
of truth afterwards.

## Input expectations

LaTeXtify targets *manuscripts that use Word styles*: styled headings,
equation-editor math, inline figures with captions. Unsupported constructs
(text boxes, SmartArt, tracked changes) are reported by preflight rather
than silently mangled.

## Development

```
uv run pytest                                     # full suite (~650 tests, real docx→PDF compiles)
uv run pytest -m "not tectonic and not network"   # fast subset
uv run ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the archived build plan under
`plans/archive/` for architecture documentation.

## License

Apache-2.0 (see [LICENSE](LICENSE)). Vendored LaTeX class/style files under
`latextify/templates/journals/*/vendor/` remain under their own licenses
(LPPL) — see [NOTICE](NOTICE) and the per-journal `VENDOR_LICENSE.txt` files.
