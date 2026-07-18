---
title: LaTeXtify
emoji: 📄
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Word (.docx) manuscripts to journal-ready LaTeX + PDF
---

# LaTeXtify — public demo

Convert a scientific manuscript from Word (`.docx`) into a journal-ready
LaTeX project and compiled PDF. Pick a journal (APS, AIP, Elsevier, IEEE,
Springer Nature, ACS, IOP, Wiley…), drop your files, and download the LaTeX
project as a `.zip` plus the compiled PDF — citations extracted from
Zotero/Mendeley/EndNote/Word field codes or reconstructed via Crossref,
figures and equations converted, and an honest `report.md` punch list.

## ⚠️ This is a shared demo

- Uploads are processed on this shared Space and **deleted within an hour**,
  but you should still treat it as a public demo: **do not upload
  confidential or unpublished manuscripts** you would not email to a stranger.
- Uploads are capped at **25 MB per file** and conversions are
  **rate-limited per visitor**. Conversions run one at a time, so the page
  may pause while someone else's paper compiles.

## Private, unlimited use

LaTeXtify is a local-first tool — install it and everything runs on your own
machine, nothing leaves it:

```bash
pip install "latextify[gui] @ git+https://github.com/pquarterman17/LaTeXtify"
latextify gui          # opens the same UI at http://127.0.0.1:8501
latextify convert paper.docx --journal revtex4-2 --pdf   # or use the CLI
```

Source, issue tracker, and full documentation:
**<https://github.com/pquarterman17/LaTeXtify>** (Apache-2.0).
