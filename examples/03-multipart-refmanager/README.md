# Example 03 — Multi-part submission with a reference manager

The full journal-submission shape: a main manuscript, a separate
**Supplementary Material** document, reference-manager citations, and an
explicit metadata sidecar.

```
03-multipart-refmanager/
├── make_manuscript.py   # generates everything below
├── run.py               # generate + convert (with --supplement) to PDF
│                        # ── generated ──
├── main.docx            # citations = Zotero + Mendeley field codes
├── supplement.docx      # citations = Zotero field codes
└── paper.yaml           # explicit title/authors/affiliations
```

## Run it

```bash
python run.py
```

Equivalent to:

```bash
latextify convert main.docx --journal revtex4-2 \
    --supplement supplement.docx --output output --pdf
```

Output: `output/revtex4-2/` with **two** write-once documents —
`main.tex` → `main.pdf` and `supplement.tex` → `supplement.pdf` — sharing one
`references.bib` and one `figures/` directory.

## What this demonstrates

### Reference-manager citations (Cite While You Write)

The citations are **Word field codes**, exactly what the Zotero and Mendeley
Word plugins embed when you insert a citation:

- `ADDIN ZOTERO_ITEM CSL_CITATION { …CSL-JSON… }` (Zotero)
- `ADDIN CSL_CITATION { …CSL-JSON… }` (Mendeley)

Each field carries the complete bibliographic record — authors, title,
journal, volume, pages, **DOI**, year. LaTeXtify reads those records directly
and writes `references.bib` from them, so the bibliography is built **with no
Crossref lookup and no network** — every entry is a full `@article` with a
DOI. EndNote (`ADDIN EN.CITE`) and Word's built-in citation manager are
recognised too.

> **Important:** LaTeXtify does **not** read a reference-manager *library
> file* — there is no `--bib`, no `.ris`/`.enl` import, no CSL-JSON file
> input. The metadata must be **embedded in the document** as field codes
> (which is what the Word plugins do automatically). If instead you only have
> a plain manuscript plus an exported library, you fall back to the typed
> reference list + Crossref path shown in
> [example 01](../01-all-embedded/) — which does not consult your library.

### `--supplement`: two documents, one shared bibliography

`supplement.docx` runs through the same pipeline into the same output folder.
Its figures would land as `figS1`, `figS2`, … in the shared `figures/`; its
citations are **merged into the shared `references.bib`, de-duplicated by
DOI**. Cornelissen 2015 is cited in *both* documents but appears **once** in
`references.bib`. Check `report.md`:

```
## Supplement
`supplement.tex` written.
SI citations: 2 (1 new reference(s) added to references.bib; the rest were
deduplicated against the main document's bibliography).
```

So `references.bib` holds three unique entries (Cornelissen, Chumak,
Kajiwara), each with its DOI.

### `paper.yaml`: explicit metadata

Instead of guessing the title page, this example ships a `paper.yaml` sidecar
that LaTeXtify reads verbatim — the precise way to control authors,
affiliation numbering, the corresponding-author flag, abstract, and keywords:

```yaml
title: Non-local Magnon Spin Transport in Thin-Film Insulators
affiliations:
  - Institute for Spintronics, Example University, Springfield, USA
  - National Laboratory for Materials, Metropolis, USA
authors:
  - name: Dana R. Leadauthor
    affiliations: [1]
    email: dana.leadauthor@example.edu
    corresponding: true
  - name: Evan S. Coauthor
    affiliations: [1, 2]
abstract: >-
  We study non-local magnon spin transport ...
keywords: [magnon spintronics, spin transport]
```

The supplement inherits `Supplementary Material: <title>` and the same author
block automatically (no metadata guessing runs on the SI document).

## Notes

- `paper.yaml` lives beside the **main** document and applies to it. When you
  convert a single document without a `paper.yaml`, LaTeXtify guesses one from
  the title page and writes it once for you to edit (see example 01).
- The PDF step needs [Tectonic](https://tectonic-typesetting.github.io/);
  without it, `run.py` still emits both LaTeX documents and tells you so.
