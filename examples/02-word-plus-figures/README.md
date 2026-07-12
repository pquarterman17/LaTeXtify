# Example 02 — Word + separate figures

The Word file carries small, low-resolution **draft** figures, but the real,
publication-quality figures are kept as separate files beside it. LaTeXtify
substitutes the external files for the embedded placeholders at conversion
time, using two conventions that live next to `paper.docx`:

```
02-word-plus-figures/
├── make_manuscript.py   # generates everything below
├── run.py               # generate + convert to PDF
│                        # ── generated ──
├── paper.docx           # grey low-res placeholders embedded inline
├── figures/
│   ├── fig1.png         # folder convention -> overrides figure 1
│   └── panels/
│       └── detector-signal.png   # target of the manifest entry
└── figures.yaml         # explicit map -> overrides figure 2
```

## Run it

```bash
python run.py
```

Equivalent to:

```bash
latextify convert paper.docx --journal revtex4-2 --output output --pdf
```

## What this demonstrates — the figure override tiers

For each figure number, LaTeXtify resolves the file to use in this order
(first match wins):

| Tier | Where | This example |
|------|-------|--------------|
| 1. **Manifest** | a `figures.yaml` next to the `.docx`: `{ <number>: <path> }` | figure **2** → `figures/panels/detector-signal.png` |
| 2. **Folder** | a `figures/fig<N>.<ext>` file next to the `.docx` | figure **1** → `figures/fig1.png` |
| 3. **Embedded** | the image pasted into the Word file | (the grey placeholders — overridden here) |

A number listed in `figures.yaml` is taken from there even if a matching
`figures/figN.*` file also exists. When several `figures/figN.*` files exist
for one number, the extension priority is `pdf > eps > svg > png > jpg`
(LaTeX prefers vector formats).

Check `output/revtex4-2/report.md` — it records each figure's provenance:

```
## Figures
**Fig 1** (OVERRIDE)
**Fig 2** (MANIFEST)
```

The green/blue images that land in `output/revtex4-2/figures/` are the
external files, not the grey embedded placeholders.

## Notes

- **The manifest is path-only.** `figures.yaml` maps a number to a file; it
  does **not** set captions, widths, or placement. Captions always come from
  the Word document (`Figure N:` / `Fig. N:` paragraphs or the Caption style),
  width is `\linewidth` (landscape images auto-promote to a two-column
  `figure*`), and placement is fixed.
- **Supply vector figures for print quality.** Drop a `figures/fig1.pdf`
  (or `.eps`/`.svg`) instead of a PNG and it wins by extension priority.
  EPS/SVG are converted to PDF for inclusion (EPS needs Ghostscript on PATH).
- The PDF step needs [Tectonic](https://tectonic-typesetting.github.io/);
  without it, `run.py` still emits the LaTeX project and tells you so.
