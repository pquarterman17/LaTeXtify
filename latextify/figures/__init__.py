"""Figure handling: extract embedded images, resolve user-file overrides.

Override resolution order (plan items 9, 15):
    1. figures.yaml manifest entry (explicit mapping, wins on conflict)
    2. figures/fig<N>.(pdf|eps|svg|png|jpg) next to the .docx, matched by
       figure number
    3. the image embedded in the .docx (fallback)

Planned modules:
    extract.py  -- associate extracted media with figure numbers + captions
                   (Caption style, or "Figure N:" / "Fig. N" text patterns)
    override.py -- folder-convention + manifest resolution, report lines
    convert.py  -- SVG->PDF conversion for LaTeX inclusion, EPS passthrough
"""
