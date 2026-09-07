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

## Stage 1 — diagnostics

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

## Stage 2 — input mechanisms (built)

Fixes gap 3, and turns the caption-gap warning into a repair.

All four layer onto the one existing resolution step rather than becoming
parallel paths. `OverrideSources` (an explicit `{number: path}` map plus an
ordered list of directories) is the only new concept; `resolve_overrides`
consults it, and `_resolve_one` holds the whole tier order in one readable
place:

1. `--figure N=PATH` → `FigureSource.EXPLICIT`
2. a `fig<N>.<ext>` in `--figures-dir`, then in the split `--figures-pdf`
3. `figures.yaml`
4. `figures/fig<N>.<ext>` beside the manuscript
5. the embedded image

`--figures-pdf` needs no rules of its own: it is split with pypdf into a
temporary directory of `fig<N>.pdf` and handed to the same directory tier,
which is why one mechanism covers two options. `build_sources` is a context
manager so that staging directory outlives the conversion and nothing more.

### Caption-gap repair — better than expected

The known risk was real but the shape of the problem turned out to help.
VERIFIED on a fixture captioning 1-4 with no image for 3:

- pandoc leaves the orphaned `Figure 3: ...` paragraph in the body **as
  ordinary text**, exactly where the figure belongs, so there is a precise
  anchor point to replace — no guessing at insertion position;
- LaTeX numbers floats by position, so planting the float there makes the
  printed numbering correct with no counter manipulation.

So the repair does more than fill a hole. It renumbers the extracted figures
from document order to the numbers the captions *state*, which re-pairs every
shifted caption with its own image — the mis-binding that made this bug worth
fixing in the first place.

It stays inert unless a replacement file was actually supplied for a gap.
Repairing the numbering when we cannot also supply the missing image would
silently reshape a project an author is already working with.

**A bug found while building it, worth remembering.** Resolution originally ran
before renumbering, so a file supplied for gap 3 was *also* picked up by
whichever document-order figure happened to be numbered 3 — the same file
emitted twice under two numbers, and the real figure 4 losing its own image.
Gap planning now runs first, so every figure resolves against the number it
will actually ship as. A test pins the written filenames.

**A second one:** `Figure.resolved_path` listed the override tiers by name, so
adding `EXPLICIT` left it returning the *embedded* file — `--figure N=PATH`
relabelled provenance and changed nothing that shipped. It now asks "not
EMBEDDED", so a future tier cannot reintroduce that.

### Closing the pipeline

Two gaps left between "the CLI can do it" and "the pipeline is done", both now
closed:

- **The GUI could not take a bundle.** Uploading a multi-page PDF as an
  ordinary figure used page 1 and silently discarded the rest. There is now a
  *figure bundle (PDF)* role that routes to the same `split_figures_pdf`, and
  an unreadable bundle is a 400 like any other bad upload.
  Individual uploads beat the bundle's page for their number, but write order
  cannot express that: `fig2.pdf` and `fig2.png` do not overwrite each other,
  and the folder override picks by extension priority, so the bundle's PDF won
  regardless. Staging therefore deletes `fig<N>.*` for every individually
  supplied number before writing it. A test pins the precedence.
- **`emit_project` created directories before it could reject the run.** A bad
  journal name, or a citation mode the journal does not support, left an empty
  `output/<journal>/` behind. Validation now runs first and nothing is created.
  That pushed `project.py` over the 500-line ceiling again, so
  `journal_output_dir` moved to `emit/output_dir.py` -- a clean seam, since it
  exists only to keep a caller-supplied name from steering writes out of the
  root.

## Deliberately not done

- **Harvesting figures out of a finished paper PDF.** A different feature with
  a much harder core (region detection); bundling it would blur this one.
- **Cropping a multi-page PDF's pages to their content bounding box.** Needs
  Ghostscript or pdfcrop, neither of which is a dependency. Revisit only if
  full-page margins turn out to be a real annoyance in practice.
- **A manuscript with captions and no pasted images at all.** The repair fills
  gaps in a document that still has *some* figures; a fully image-less
  manuscript would need the caption walk to drive emission outright. Not asked
  for, and a much larger change to the anchor contract.
- **Raising the DPI floor per journal.** 300 is near-universal; a per-journal
  override is easy to add later if any target actually differs.
