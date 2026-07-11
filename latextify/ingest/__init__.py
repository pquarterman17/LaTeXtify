"""Ingest stage: open the .docx, validate it, convert the body via pandoc.

A .docx is a ZIP archive; the pieces this stage reads:
    word/document.xml  -- body content (paragraphs, runs, field codes, drawings)
    word/styles.xml    -- style definitions (headings, Caption, Title)
    word/media/        -- embedded images

Planned modules (plan items 2-3):
    preflight.py  -- inventory styles, detect unsupported constructs
                     (text boxes, SmartArt, tracked changes, floating objects)
                     and emit PreflightFinding records for the report
    pandoc.py     -- pypandoc invocation: docx -> pandoc JSON AST
                     (--extract-media for images), AST -> LaTeX body
    filters.py    -- panflute AST filters: normalize heading levels, strip
                     Word artifacts, plant anchors where citations/figures go
"""
