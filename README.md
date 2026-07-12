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

Fourteen journal templates covering the major physics/chemistry publishers.
The `-j`/`--journal` value is the short id in the first column; the GUI shows
the full publisher name.

| Template | Journal | Citation modes | TeX class source |
|---|---|---|---|
| `revtex4-2` | APS — Physical Review B (generic REVTeX) | numeric | Tectonic bundle |
| `aps-prl` | APS — Physical Review Letters | numeric | Tectonic bundle |
| `aps-prx` | APS — Physical Review X | numeric | Tectonic bundle |
| `aps-prapplied` | APS — Physical Review Applied | numeric | Tectonic bundle |
| `aps-rmp` | APS — Reviews of Modern Physics | numeric | Tectonic bundle |
| `aip-apl` | AIP — Applied Physics Letters | numeric | Tectonic bundle |
| `aip-jap` | AIP — Journal of Applied Physics | numeric | Tectonic bundle |
| `aip-advances` | AIP — AIP Advances | numeric | Tectonic bundle |
| `elsarticle` | Elsevier | numeric, author-year | vendored (v3.5, LPPL) |
| `ieeetran` | IEEE | numeric | Tectonic bundle |
| `sn-jnl` | Springer Nature | numeric, author-year | vendored (LPPL) |
| `achemso` | ACS | numeric | Tectonic bundle |
| `iopart` | IOP Publishing | numeric | vendored (LPPL) |
| `wiley` | Wiley (New Journal Design) | numeric, author-year | user-supplied¹ |

The APS and AIP entries are REVTeX variants — the same `revtex4-2` class with
the publisher's society options and bibliography style, so they compile with
no extra class files.

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

# ...and staple the main text + supplement into one combined.pdf
uv run latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf --combine-supplement

# ...or render the supplement as a simplified one-column article (common for SI,
# where formatting rules are looser), keeping the shared bibliography + S-numbers
uv run latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf --supplement-onecolumn

# A folder of manuscripts at once (continue-on-error + summary)
uv run latextify batch drafts/ --journal revtex4-2 --pdf

# Equation conversion audit for equation-heavy papers
uv run latextify equations paper.docx --pdf

# Local web GUI (drag-and-drop; requires the gui extra: uv sync --extra gui)
uv run latextify gui

# List registered journals and their citation modes
uv run latextify journals
```

### Web GUI

`uv run latextify gui` starts a local, browser-based front end (bound to
`127.0.0.1` only — your uploads never leave your machine) and opens a tab.
Drop your whole submission in at once — **main `.docx`, supplement `.docx`,
figure files, and a `.bib` reference library together** — then set each
file's role, pick a journal from the full publisher list, choose options
(compile PDF, combine supplement, one-column SI, equation audit, project
`.zip`), and convert. Preview the compiled PDFs inline. An optional **Export**
panel lets you pick a destination folder (a native "Browse…" dialog) and copy
any subset of the outputs — the LaTeX project, individual PDFs, or the `.zip` —
straight to where you want them.

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

## Examples

Three runnable, self-contained examples live in [`examples/`](examples/) —
each generates its own Word document(s) (no committed binaries) and converts
them to a PDF with a single `python run.py`:

- [01 – all-embedded](examples/01-all-embedded/): one `.docx` with embedded
  figures and a typed reference list (Crossref reconstruction).
- [02 – word-plus-figures](examples/02-word-plus-figures/): external figure
  files via the `figures/` folder convention and a `figures.yaml` manifest.
- [03 – multipart-refmanager](examples/03-multipart-refmanager/): main +
  `--supplement` documents with Zotero/Mendeley field-code citations and a
  shared, de-duplicated bibliography.

## Input expectations

LaTeXtify targets *manuscripts that use Word styles*: styled headings,
equation-editor math, inline figures with captions. Unsupported constructs
(text boxes, SmartArt, tracked changes) are reported by preflight rather
than silently mangled.

## Development

```
uv run pytest                                     # full suite (~875 tests, real docx→PDF compiles)
uv run pytest -m "not tectonic and not network"   # fast subset
uv run ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and the archived build plan under
`plans/archive/` for architecture documentation.

## License

Apache-2.0 (see [LICENSE](LICENSE)). Vendored LaTeX class/style files under
`latextify/templates/journals/*/vendor/` remain under their own licenses
(LPPL) — see [NOTICE](NOTICE) and the per-journal `VENDOR_LICENSE.txt` files.
