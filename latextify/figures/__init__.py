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

``override.py``'s ``find_override``/``resolve_overrides`` and
``convert.py``'s ``convert_for_latex`` all accept an optional ``prefix``
(default ``""``) so a supplementary-material document's figures (plan item
21) can be resolved/copied as ``figS<N>.<ext>`` -- the folder-convention
counterpart, ``figures/figS<N>.<ext>``, works exactly like the main
document's ``figures/fig<N>.<ext>`` -- without colliding with the main
document's own ``fig<N>.<ext>`` files in the same shared ``figures/``
directory.
"""
