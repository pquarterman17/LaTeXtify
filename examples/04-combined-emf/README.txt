LaTeXtify combined Word + EMF acceptance sample
================================================

Combined-Manuscript-with-EMF.docx contains synthetic main text followed by a
"Supplementary Information" heading and two real embedded Windows EMF figures.
It contains no research data.

Recommended GUI choices:

  Combined main + supplement: Main two columns; supplement one column
  Figure 1 width:              One column
  Figure 2 width:              Two columns
  Compile PDF:                 On

The output should be one main.pdf with a page break before the supplement,
S-numbering after the boundary, and both figures visible. Without LibreOffice
or Inkscape, LaTeXtify should report that the EMFs were rasterized at 600 DPI.

make_sample.py regenerates the document on Windows and is not needed to use it.
