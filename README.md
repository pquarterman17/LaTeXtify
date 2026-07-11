# LaTeXtify

Convert scientific manuscripts from Word (.docx) to journal-ready LaTeX
projects and PDFs. Pick a journal (APS/AIP REVTeX, Elsevier, Nature/Springer,
IEEE), point at a .docx, get a compilable LaTeX project with extracted
citations (BibTeX + DOI hyperlinks), journal-appropriate citation style,
figures (embedded or replaced with your own vector files), and a PDF built
with Tectonic — no TeX installation required.

**Status: pre-implementation.** See `plans/LATEXTIFY_PLAN.md` for the full
plan, architecture, and execution roadmap.

## Planned usage

```
latextify paper.docx --journal prb
latextify paper.docx --journal elsarticle --citation-style authoryear
```

## Layout

```
latextify/    Python package (core library + CLI)
plans/        project plan (source of truth for what to build next)
tests/        pytest suite + fixture .docx corpus
```

## Development

```
uv sync            # create venv + install deps
uv run pytest      # run tests
uv run ruff check  # lint
```
