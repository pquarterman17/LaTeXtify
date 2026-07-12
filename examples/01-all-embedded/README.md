# Example 01 — Everything embedded in one Word file

The simplest, most common shape: a single `.docx` that already contains
**everything** — title page, abstract, body, figures pasted into the
document, in-text citation markers, and a typed reference list. Nothing sits
beside it.

```
01-all-embedded/
├── make_manuscript.py   # generates paper.docx (run by run.py)
└── run.py               # generate + convert to PDF
```

## Run it

```bash
python run.py
```

That regenerates `paper.docx`, then runs the equivalent of:

```bash
latextify convert paper.docx --journal revtex4-2 --output output --pdf
```

Output lands in `output/revtex4-2/`:

```
output/revtex4-2/
├── main.tex            # \input-s the generated pieces below (write-once)
├── main.pdf            # compiled with Tectonic (if available)
├── references.bib      # reconstructed bibliography
├── figures/            # fig1.png, fig2.png — extracted from the .docx
└── generated/          # preamble.tex, metadata.tex, body.tex, bibliography.tex
```

## What this demonstrates

- **Embedded figures.** The two images pasted into the Word file are
  extracted to `output/revtex4-2/figures/fig1.png` and `fig2.png`, and their
  `Figure N:` caption paragraphs become real `\caption{}`s.
- **Metadata guessing.** Title, the two authors, and their affiliations are
  read straight from the title-page structure — no configuration. (LaTeXtify
  also writes a `paper.yaml` next to the `.docx` the first time; edit it to
  correct any guess and it is reused on the next run.)
- **Plain-text citations → Crossref.** There are no reference-manager field
  codes here, just typed `[1]`/`[2]` markers and a typed reference list.
  LaTeXtify reconstructs the bibliography by querying Crossref:
  - A confident match becomes a full BibTeX entry **with a DOI** (reference
    **[2]**, Cornelissen *et al.*, resolves to `10.1038/nphys3465`).
  - A borderline match (below the 0.72 confidence threshold) is emitted as a
    raw entry **flagged for you to verify** — you'll see a `verify` warning on
    the console. This is the safety net, not a bug.

  Your exact matches depend on Crossref and may differ run to run. **Offline,
  every reference falls back to a verify-flagged raw entry and the document
  still compiles.** For citations that are always clean and need no network,
  use a reference manager's Word plugin — see
  [example 03](../03-multipart-refmanager/).

## Notes

- The PDF step needs [Tectonic](https://tectonic-typesetting.github.io/). If
  the machine is offline with no cached engine, `run.py` still emits the LaTeX
  project and tells you so; compile later where Tectonic is available (or use
  an [offline kit](../../latextify/kit/README-OFFLINE.md)).
- Figures are auto-numbered by LaTeX. In-text "Figure 1" written in Word
  survives as literal text — LaTeXtify does not (yet) convert Word figure
  cross-references into live `\ref`s.
