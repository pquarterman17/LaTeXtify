"""Figure handling: extract embedded images, resolve user-file overrides, convert.

Override resolution order (plan items 9, 15 -- both implemented):
    1. figures.yaml manifest entry (explicit mapping, wins on conflict)
    2. figures/fig<N>.(pdf|eps|svg|png|jpg) next to the .docx, matched by
       figure number
    3. the image embedded in the .docx (fallback)

Modules:
    extract.py  -- associate extracted media with figure numbers + captions
                   (Caption style, or "Figure N:" / "Fig. N" text patterns)
    override.py -- folder-convention + figures.yaml manifest resolution
    convert.py  -- SVG->PDF conversion for LaTeX inclusion (cairosvg, falling
                   back to svglib+reportlab on Windows DLL failure) and EPS
                   handling. VERIFIED (item 15): Tectonic does NOT support
                   raw EPS inclusion at all ("PostScript images are not
                   supported by Tectonic") -- EPS is converted via
                   Ghostscript when found on PATH, else an actionable
                   EmitWarning is raised naming the fix.
"""
