"""Ingest stage: open the .docx, validate it, convert the body via pandoc.

A .docx is a ZIP archive; the pieces this stage reads:
    word/document.xml  -- body content (paragraphs, runs, field codes, drawings)
    word/styles.xml    -- style definitions (headings, Caption, Title)
    word/media/        -- embedded images

Modules (plan items 2-3):
    preflight.py  -- inventory styles, detect unsupported constructs
                     (text boxes, SmartArt, tracked changes, floating objects)
                     and emit PreflightFinding records for the report
    pandoc.py     -- pypandoc invocation: docx -> pandoc JSON AST
                     (--extract-media for images), AST -> LaTeX body
    filters.py    -- the AST filter pipeline and its order: strip Word
                     artifacts, associate captions, plant anchors where
                     citations/figures go, and ``apply_all`` that runs it
    headings.py   -- recovering section structure a manuscript typed instead
                     of styling, and clamping levels to 1..3
    tables.py     -- Word tables -> LaTeX tabular/longtable, content-inferred
                     column alignment, wide-table float promotion
    tables_degraded.py -- reconstruction for tables with no honest tabular
                     equivalent (nested, pathological merged-cell grids),
                     marked as simplified rather than silently corrupted
"""
