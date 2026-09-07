# LaTeXtify

[![PyPI](https://img.shields.io/pypi/v/latextify)](https://pypi.org/project/latextify/)
[![CI](https://github.com/pquarterman17/LaTeXtify/actions/workflows/ci.yml/badge.svg)](https://github.com/pquarterman17/LaTeXtify/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

Convert scientific manuscripts from Word (`.docx`) into journal-ready LaTeX
projects and PDFs. Pick a journal, point at a manuscript, get a compilable
LaTeX project with extracted citations, journal-appropriate formatting, and
a compiled PDF — **no TeX installation required** (the Tectonic engine is
downloaded and managed automatically).

```
latextify convert paper.docx --journal revtex4-2 --pdf
```

**Try it online:** <https://latextify-demo.onrender.com> — a free-hosted demo
of the GUI (shared low-power hardware: conversions are slow, uploads capped at
25 MB, rate-limited, and the instance takes ~1 min to wake after idle). Do
**not** upload confidential or unpublished manuscripts to the demo; install
locally for private, full-speed conversions.

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
- **Reference checking + review** *(opt-in, needs internet)* —
  `--check-references` validates every reference against Crossref: it resolves
  each DOI and compares the stored title, authors, year, journal, volume, and
  pages against the canonical record, flags dead DOIs, and suggests DOIs for
  references that lack one. Findings land in `report.md`; a dropped connection
  marks references *unchecked* rather than failing the run. Add `--review` (or
  the GUI's review panel) to step through each flagged reference one by one —
  **approve** Crossref's fix, **keep** yours, or **edit** the whole entry — then
  the accepted corrections are written into `references.bib` and the PDF is
  recompiled. On by default in the GUI.
- **Figures** — extracts embedded images, or swap in your own
  vector/high-res files by dropping `figures/fig1.pdf` beside the docx (or
  an explicit `figures.yaml`). SVG converts to PDF automatically.
  `latextify figures paper.docx` lists what you have and what it will print
  at — see [Replacing pasted screenshots with vector art](#replacing-pasted-screenshots-with-vector-art).
  Embedded camera metadata — GPS, body/lens serial numbers, the photographer's
  name, the capture thumbnail — is stripped on the way into `figures/`,
  losslessly: pixels and colour profile are untouched.
  `--keep-figure-metadata` opts out.
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

## Replacing pasted screenshots with vector art

A figure pasted into Word as a screenshot is pixels forever. Printed at column
width it is usually well under the 300 DPI journals ask for, and no conversion
step can recover detail that was never captured. The fix is to supply the
original vector file — LaTeXtify will use it instead of what is in the
document.

### 1. See what you actually have

```bash
latextify figures paper.docx
```

```
3 figure(s) in paper.docx

FIG  SOURCE    KIND        SIZE           DPI       CAPTION
  1  embedded  raster      1100x900       324       Device schematic and measurement setup.
  2  embedded  raster      640x480        91 LOW    Raman spectra of the three samples.
  3  override  raster-pdf  468x351pt      109 LOW   Temperature dependence of the resistance.

0 of 3 already vector.
Figure(s) 2, 3 would print below 300 DPI. Supply a vector version of each as figures/fig<N>.pdf (or .eps/.svg) beside the manuscript.
```

Three things this tells you that guessing cannot:

- **Which figure is number 3.** Figures are numbered by position in the
  document, which is *not* always what a caption says — a caption with no image
  behind it shifts every later number, and the listing warns when it finds one.
  The number is what names the replacement file, so getting it wrong silently
  swaps two figures in your submission.
- **What each file really is.** `raster-pdf` above means a PDF whose content is
  a single image and no text: a screenshot someone printed to PDF. It has a
  vector file's extension and a raster's limits — note that figure 3 is an
  `override`, i.e. a file someone supplied *believing* it was vector.
- **How it will print.** DPI is the effective resolution at the width it will
  be printed at, flagged `LOW` below 300. A landscape figure is assumed to span
  both columns, so it needs roughly twice the pixels of a single-column one —
  which is why figure 2 above fails at 640x480 while figure 1 passes at
  1100x900. Any crop you applied in Word is accounted for, since those pixels
  never reach the output.

The same table is in the web GUI under *What are my figures?*, and
`--json` makes it scriptable.

### 2. Drop the vector files in

Put them beside the manuscript, named by figure number:

```
paper.docx
figures/
  fig2.pdf
  fig3.svg
```

PDF, EPS and SVG are all accepted; SVG and EPS are converted to PDF for you.
A vector file always beats the embedded image, and beats a raster of the same
number. For names that don't follow the convention, map them explicitly in a
`figures.yaml` beside the docx:

```yaml
2: plots/raman-spectra.pdf
3: plots/resistance-vs-temperature.pdf
```

### 3. Convert, and check it took

```bash
latextify convert paper.docx -j revtex4-2 --pdf --vector-figures
```

`--vector-figures` reports every figure that is *still* raster, with its print
DPI and the exact filename to supply:

```
warning: figure 3 appears to be a raster image wrapped in a PDF -- about 109 DPI
at 7 in wide, below the 300 DPI most journals require. Supply a vector version
as figures/fig3.pdf (PDF, EPS or SVG) to replace it.
```

`report.md` records the same thing durably, per figure:

```
**Fig 1** (EMBEDDED, raster 324 DPI at 3.4 in)
**Fig 2** (EMBEDDED, raster 91 DPI at 7 in — below 300 DPI)
**Fig 3** (OVERRIDE, raster in a PDF 109 DPI at 7 in — below 300 DPI)
```

### Getting vector out of Word in the first place

Word is not the problem as often as it seems. It cannot hold vector for a
screen capture — that is raster the moment you press Print Screen — but it
holds real vector for anything drawn:

- **Paste Special, not Ctrl+V.** Copy a chart from Excel, Origin, MATLAB or
  Illustrator, then Home ▸ Paste ▸ Paste Special ▸ *Picture (Enhanced
  Metafile)*. That lands as EMF, which is vector. A plain Ctrl+V usually gives
  you a bitmap.
- **LaTeXtify converts EMF and WMF to PDF** when LibreOffice or Inkscape is on
  your PATH. Neither is a dependency — without one, the figure is skipped and
  the warning names the fix.
- **Export from the plotting tool.** Saving directly to PDF or SVG and dropping
  it in as `figures/fig<N>.pdf` skips Word entirely and is the most reliable
  route.
- **A micrograph or photograph has no vector form.** Supply it at a size that
  reaches 300 DPI at print width instead — roughly 1000 px across for a
  single-column figure, 2100 px for one spanning both columns. `latextify
  figures` reports exactly this, so you can check before submitting rather than
  after a desk rejection.

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

Install from [PyPI](https://pypi.org/project/latextify/) with pip (Python 3.10+):

```
pip install latextify
```

That is the whole install: **no TeX setup** (the Tectonic engine is fetched and
managed automatically the first time you use `--pdf`), and pandoc ships bundled
in the wheel. The `latextify` command is then on your PATH. Also installable
with `uv pip install latextify` or `pipx install latextify`.

For the drag-and-drop web GUI, add the `gui` extra:

```
pip install "latextify[gui]"
```

### From source (development)

```
git clone https://github.com/pquarterman17/LaTeXtify
cd LaTeXtify
uv sync
```

### One-click launcher

To start the web GUI without touching a command line, double-click
**`startup.bat`** (Windows) or run **`./startup.sh`** (macOS/Linux). It installs
dependencies on first run, opens the GUI in your browser, and — if anything
goes wrong — writes an easy-to-find `latextify-startup.log` next to the script
and prints it for you to copy.

## Usage

> Installed from source with `uv sync`? Prefix these with `uv run` (e.g.
> `uv run latextify convert ...`), or activate the project venv first.

```
# Convert + compile to PDF (report.md written alongside)
latextify convert paper.docx --journal revtex4-2 --pdf

# Choose citation style where the journal supports both
latextify convert paper.docx --journal elsarticle --citation-style authoryear

# Supplementary material: S-numbered second document sharing the bibliography
latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf

# ...and staple the main text + supplement into one combined.pdf
latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf --combine-supplement

# ...or render the supplement as a simplified one-column article (common for SI,
# where formatting rules are looser), keeping the shared bibliography + S-numbers
latextify convert paper.docx --journal revtex4-2 --supplement si.docx --pdf --supplement-onecolumn

# Validate references online against Crossref (DOIs, titles, authors, years...)
latextify convert paper.docx --journal revtex4-2 --check-references

# ...and interactively approve/deny/edit each correction, then rewrite + recompile
latextify convert paper.docx --journal revtex4-2 --pdf --review

# List a manuscript's figures: number, caption, format, effective print DPI
latextify figures paper.docx

# Convert, reporting every figure that is still a screenshot rather than vector
latextify convert paper.docx --journal revtex4-2 --pdf --vector-figures

# A folder of manuscripts at once (continue-on-error + summary)
latextify batch drafts/ --journal revtex4-2 --pdf

# Equation conversion audit for equation-heavy papers
latextify equations paper.docx --pdf

# Local web GUI (drag-and-drop; requires the gui extra: pip install "latextify[gui]")
latextify gui

# List registered journals and their citation modes
latextify journals
```

### Web GUI

`latextify gui` starts a local, browser-based front end (bound to
`127.0.0.1` only — your uploads never leave your machine) and opens a tab.
Drop your whole submission in at once — **main `.docx`, supplement `.docx`,
figure files, and a `.bib` reference library together** — then set each
file's role, pick a journal from the full publisher list, choose options
(compile PDF, combine supplement, one-column SI, equation audit, project
`.zip`), and click **Preview**. The compiled PDFs render inline so you can
confirm the conversion worked. A preview is held only in **temporary local
storage** (a private working directory the app owns) — it is pruned
automatically about an hour after its last use and deleted when the app shuts
down, so nothing lingers unless you keep it. Once it looks good, the **Export**
panel lets you pick a destination folder (a native "Browse…" dialog) and copy
any subset of the outputs — the LaTeX project, individual PDFs, or the `.zip` —
to the folder you choose to keep.

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
