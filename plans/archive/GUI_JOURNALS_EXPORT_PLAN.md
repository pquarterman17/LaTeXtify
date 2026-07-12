# LaTeXtify — Journal names/variants, one-column SI, export location

Round-two GUI polish after the multi-file intake shipped: proper publisher
names in the journal dropdown, more journal options (APS/AIP REVTeX variants),
a simplified one-column supplement, and a real "choose where to save" export
flow with per-artifact selection. Plus the IEEEtran title-page bug (fixed) and
its regression guard.

**Status:** Complete
**Created:** 2026-07-12
**Updated:** 2026-07-12

---

## Context

### How the pieces fit together

- `latextify/templates/loader.py` — validates each journal `manifest.yaml` into
  a `Journal`. Gains a `display_name` field and a `templates_from` key so a
  variant manifest can reuse another journal's `.j2` templates (no duplication).
- `latextify/templates/journals/<name>/` — one folder per journal. REVTeX
  variants (PRL/PRX/PR Applied/RMP, APL/JAP/AIP Advances) are manifest-only:
  same class (`revtex4-2`), different `class_options`, `templates_from: revtex4-2`.
- `latextify/gui/server.py` — `/api/journals` returns `display_name`; a new
  native folder-picker endpoint + the multi-file convert copies selected
  artifacts to a chosen folder.
- `latextify/gui/static/index.html` — dropdown shows display names; an Export
  panel adds a Browse button + per-artifact checkboxes.
- `latextify/emit/project.py::_emit_supplement` — gains a one-column "plain
  article" supplement mode (`\documentclass[11pt]{article}`), keeping the
  shared bib/figures + S-numbering.
- `latextify/cli.py` — `--supplement-onecolumn` flag threaded to emit.

### Resolved decisions (2026-07-12, via GUI round-two Q&A)

- **Export**: native Browse dialog to pick a save folder + checkboxes for which
  artifact types (LaTeX project / main PDF / combined PDF / audit PDF / .zip) to
  write there. (Server runs on the user's own machine, so a server-side native
  folder picker is legitimate.)
- **One-column SI**: plain `article` class (11pt), simplest "less strict" format.
- **Journals**: proper publisher display names for all, plus APS + AIP REVTeX
  variants (same class, different options — cheap). New publisher classes
  (Nature/RSC) explicitly deferred.

### Dependency map

- Item 1 (loader: display_name + templates_from) → items 2, 3.
- Item 2 (display names on existing 7) and item 3 (variant manifests) both need 1.
- Item 5 (frontend) needs item 4's endpoint contract + item 2's display names.
- Items 6, 7 (one-column SI) are independent of the journal/export work.

---

## Completed

- ~~**#4 Export endpoint**~~ (2026-07-12) — `POST /api/pick-folder` opens a
  native folder dialog on the server host via a tkinter subprocess (isolated
  from uvicorn's worker thread; returns "" when cancelled/headless, never
  raises). `/api/convert-multi` grew `export_dir` + repeated `export_types`
  form fields; `_export_artifacts` copies the selected types (project tree /
  main.pdf / combined.pdf / audit.pdf / .zip on-demand) into the destination,
  reporting a requested-but-unproduced type as a warning rather than failing.
  Tests in `tests/test_gui.py`.
- ~~**#5 Export + display-name frontend**~~ (2026-07-12) — Export panel with a
  Browse button (POSTs `/api/pick-folder`, fills the folder field), a
  destination text field, and per-artifact checkboxes; `buildFormData` sends
  `export_dir` + repeated `export_types`; the status line reports what was
  exported where. Dropdown display names shipped with #2. DOM smoke test in
  `tests/test_gui.py`.

- ~~**#6/#7 One-column plain-article supplement**~~ (2026-07-12) —
  `emit_project(supplement_onecolumn=True)` renders the SI as
  `\documentclass[11pt]{article}` (natbib + unsrtnat, one-column figure env, a
  wrapped plain `\title`/`\author`/`\maketitle` block via
  `_plain_article_metadata`) instead of the journal class, keeping S-numbering +
  shared references/figures. CLI `--supplement-onecolumn` (requires
  `--supplement`) + GUI toggle + `/api/convert-multi` form field. Compiles the
  a real supplement to a clean one-column PDF. Tests in `tests/test_supplement.py`,
  `tests/test_pdf_combine.py`.
- ~~**#1 Loader: display_name + extends**~~ (2026-07-12) — `Journal` gains
  `display_name` (defaults to the folder name) and `template_root`; a manifest
  may `extends: <base>` to inherit that journal's whole manifest + `.tex.j2`
  templates, overriding only the top-level keys it restates (one level, no
  cycles). Chose `extends` over the sketched `templates_from` (DRYer — a variant
  is 3 lines). Tests in `tests/test_templates.py`.
- ~~**#2 Proper display names + endpoint + dropdown**~~ (2026-07-12) —
  `display_name` added to all 7 base manifests (publisher + journal);
  `/api/journals` returns it and sorts by it; the frontend renders it as the
  option label (value stays the id). Tests in `tests/test_gui.py`.
- ~~**#3 APS + AIP REVTeX variants**~~ (2026-07-12) — 7 manifest-only journals
  via `extends: revtex4-2`: aps-prl/prx/prapplied/rmp (inherit apsrev4-2) and
  aip-apl/jap/advances (override to aipnum4-2). All 13 compilable journals pass
  the layout sweep (14 total incl. xfailed wiley).
- ~~**IEEEtran title-block on page 2**~~ (2026-07-12) — metadata template
  emitted `\begin{abstract}` before `\maketitle`; IEEEtran renders the abstract
  inline, so the title got pushed to page 2. Reordered `\maketitle` first.
  Added `tests/test_journal_pdf_layout.py`: a tectonic sweep that compiles a
  manuscript through EVERY journal and asserts the title lands on page 1
  (wiley xfailed — `WileyNJD-v2.cls` unbundled, surfaced by the sweep).
