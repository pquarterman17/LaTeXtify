# Vector figures plan

Replacing pasted screenshots with author-supplied vector art, and being able to
see that it worked.

## The problem

A manuscript arrives with five figures pasted into Word. Word holds them as
raster (a screen capture is pixels the moment it is taken, and a plain Ctrl+V
of a chart usually lands as a bitmap too), so the compiled PDF prints them at
whatever resolution was captured — often well under the 300 DPI journals ask
for. The author has the original vector files and wants them used instead.

The swap itself was **already implemented** before this plan, and had been for
some time:

- `figures/fig<N>.<ext>` beside the manuscript overrides figure `N`
- `figures.yaml` maps figure numbers to arbitrary paths
- extension priority is `pdf > eps > svg > png > jpg`, so vector always wins
- SVG, EPS, EMF and WMF are converted to PDF on the way in
- the GUI already accepts figure uploads with explicit numbers

What was missing was everything around it. Four gaps, all confirmed with the
owner before any code was written:

1. **Which figure is number N?** Numbering is document order, and nothing
   reported it back. Naming `fig3.pdf` was a guess, and a wrong guess silently
   swaps two figures in a submission. Worse, a caption with no image behind it
   shifts every later number away from what the captions say.
2. **No confirmation.** After a conversion, nothing said which figures were
   vector and which were still screenshots.
3. **Files had to sit beside the .docx.** No way to point at a vector folder
   elsewhere.
4. **Undiscoverable.** One line in the README; nothing in the GUI.

## Stage 1 — diagnostics (this change)

Fixes gaps 1, 2 and 4, and a layout bug found while reading the code.

### `latextify.figures.inventory`

The core. Describes each figure: what the winning file **is**, how big, and how
sharp it will print.

- `classify()` — vector, raster, or `raster-in-pdf`.
- `raster-in-pdf` is called out separately because printing a screenshot to PDF
  is the most common way an author believes they supplied vector art and did
  not. Detected as: image data present, **no text extracted**, and a content
  stream short enough to be a bare image placement.
  A declared `/Font` resource proves nothing — VERIFIED 2026-09-05 that
  reportlab, and Word's own PDF export, emit `/Font` on a page holding only an
  image. Hence text *extraction*, not a resource lookup. All three conditions
  are required so a vector plot with a raster inset stays vector.
- Effective DPI is reported at a reference printed width (3.4 in single column,
  7.0 in for a figure spanning both columns), flagged below `MIN_PRINT_DPI`
  (300). Vector art reports no DPI — that is the point of supplying it.
- Two adjustments that a self-review caught, both of which silently misreported
  the one number this exists to give: a Word display crop shrinks the file that
  ships, so it is applied to the measurement (under exactly the conditions the
  emitter applies it — rasters from the document, never an override or a PDF);
  and a PDF's `/Rotate` is applied at render time, so a portrait page marked
  `/Rotate 90` measures landscape.
- A file that cannot be read is `UNKNOWN` and is never flagged. Warning about a
  file we failed to parse would be guessing.

### The wide-float bug this fixed

`emit/figures_copy._is_wide_figure` measured aspect ratio with Pillow alone, so
a **PDF could not be measured at all** and returned `False`. A landscape vector
figure — the format the override order *prefers* — therefore lost its
two-column float and was squeezed into one column. `inventory.is_wide()` now
measures PDFs through pypdf (already a dependency) and rasters through Pillow,
and `figures_copy` imports it, so the listing predicts the layout the emitter
produces. A test asserts the two are literally the same function.

### Surfaces

- **`latextify figures paper.docx`** (`cli_figures.py`) — a table of number,
  caption, source tier, kind, size and print DPI, plus caption gaps and a
  summary naming which files to create. `--json` for scripting. Writes nothing.
- **`convert --vector-figures`** — one warning per figure that is still raster,
  naming its DPI and the exact `figures/fig<N>.pdf` to supply. Opt-in, because
  a raster figure still compiles and is the right call for a micrograph;
  off by default so existing runs do not change shape. Covers supplementary
  material too: `emit_supplement` now routes through the same
  `run_figure_stage`, which also brings the SI the caption-gap check the main
  document has always had.
- **`report.md`** — each figure line gains `, vector` or
  `, raster 92 DPI at 3.4 in — below 300 DPI`. Always on, since it costs
  nothing and is the durable record.
- **GUI** — `POST /api/figures` plus a *What are my figures?* panel showing the
  same table.
- **README** — a worked section, including how to get vector out of Word
  (Paste Special ▸ Enhanced Metafile) and the honest note that a screen capture
  has no vector form.

### Reporting the output, not the source

Both the emitter and the report describe the file **written into `figures/`**,
not `Figure.resolved_path`. An SVG override has become a PDF by then;
describing the source would tell an author to replace a figure that is already
vector in what actually ships. `describe(figure, path=...)` exists for this.

### Structural note

`emit/project.py` was at 496 lines against the repo's 500-line ceiling, so the
figure stage moved to `emit/figures_stage.py` — a pure move along a seam the
file already had (`emit_project` ran the block inline). `project.py` came out
at 477 with the new option added.

## Stage 2 — input mechanisms (not yet built)

Fixes gap 3. Agreed with the owner, deliberately deferred so the diagnostics
above can be used on a real manuscript first.

- `--figures-dir PATH` — the `fig<N>` convention, folder anywhere.
- `--figure N=PATH` — repeatable explicit mapping.
- `--figures-pdf FILE` — one multi-page PDF, page N becomes figure N (split
  with pypdf).
- **Caption-gap insertion** — supplying `fig5.pdf` creates figure 5 even when
  no image was ever pasted for it.

All four layer onto the one existing resolution step rather than becoming
parallel paths, with the beside-the-docx folder and `figures.yaml` staying
lowest priority.

**Known risk on the last item.** Every figure today is emitted by resolving a
`%%FIGURE:N%%` marker planted where the image was. A figure that was never
pasted has no marker, so creating one means locating the caption paragraph in
the generated LaTeX and replacing it with a float — text surgery on generated
output. It needs real fixtures before it can be trusted, and if it turns ugly
the alternative goes back to the owner rather than being forced.

## Deliberately not done

- **Harvesting figures out of a finished paper PDF.** A different feature with
  a much harder core (region detection); bundling it would blur this one.
- **Cropping a multi-page PDF's pages to their content bounding box.** Needs
  Ghostscript or pdfcrop, neither of which is a dependency. Revisit only if
  full-page margins turn out to be a real annoyance in stage 2.
- **Raising the DPI floor per journal.** 300 is near-universal; a per-journal
  override is easy to add later if any target actually differs.
