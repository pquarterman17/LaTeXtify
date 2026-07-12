# LaTeXtify usage examples

Three self-contained, runnable examples covering the input shapes a real user
hits, from the simplest to the full journal submission. Each folder has a
`make_manuscript.py` that **generates** its Word document(s) (no binary files
are committed — the same python-docx / hand-assembled-OOXML approach the test
suite uses) and a `run.py` that generates + converts to a PDF in one step.

| # | Example | Scenario | Highlights |
|---|---------|----------|------------|
| 01 | [all-embedded](01-all-embedded/) | One `.docx` with everything inside | embedded figures · metadata guessing · typed references → **Crossref** reconstruction |
| 02 | [word-plus-figures](02-word-plus-figures/) | `.docx` + separate figure files | `figures/` folder convention · `figures.yaml` manifest · override precedence |
| 03 | [multipart-refmanager](03-multipart-refmanager/) | Main + supplement + reference manager | Zotero/Mendeley **field codes** · `--supplement` · shared/de-duplicated `references.bib` · `paper.yaml` metadata |

## Quick start

Any example runs the same way. From a checkout with LaTeXtify installed
(`uv sync`, or `pip install -e .`):

```bash
cd examples/01-all-embedded
python run.py
```

Each `run.py` regenerates its input document(s), runs the equivalent
`latextify convert … --pdf`, and prints where the output landed
(`output/revtex4-2/`). The PDF step needs
[Tectonic](https://tectonic-typesetting.github.io/); if it isn't available
(offline, no cached engine), the LaTeX project is still emitted and the script
says so — compile it later, or ship an
[offline kit](../latextify/kit/README-OFFLINE.md) that bundles Tectonic and a
warmed TeX cache.

## Which example matches your situation?

- **"I wrote the whole paper in Word, pasted my plots in, and typed my
  reference list."** → [Example 01](01-all-embedded/). Citations are
  reconstructed from the typed list via Crossref (online) and degrade to
  verify-flagged raw entries offline.
- **"My figures are separate high-resolution files; the Word doc only has
  drafts."** → [Example 02](02-word-plus-figures/). Drop files in a `figures/`
  folder or map them explicitly in `figures.yaml`.
- **"I use Zotero/Mendeley/EndNote, and I have a separate supplementary
  document."** → [Example 03](03-multipart-refmanager/). Citations ride in the
  document as reference-manager field codes (a clean, offline bibliography);
  `--supplement` emits the SI into the same project with a shared, de-duplicated
  bibliography.

## How references get in — at a glance

LaTeXtify reads bibliographic data from the manuscript itself; it does **not**
ingest a standalone `.bib`, `.ris`, or reference-manager library file.

| Your references are… | LaTeXtify path | Needs network? |
|----------------------|----------------|----------------|
| Inserted by a Zotero/Mendeley/EndNote **Word plugin** (field codes) | metadata read straight from the field codes | No — DOIs and all metadata are embedded |
| Word's **built-in** citation manager | read from the document's citation sources | No |
| A **typed** reference list + `[1]`/`(Smith 2020)` markers | reconstructed via **Crossref**, low-confidence entries flagged `verify` | Yes (offline → raw verify-flagged entries) |
| A separate exported **library file** (`.bib`/`.ris`/CSL-JSON) | **not supported** — insert citations with the Word plugin instead | — |
