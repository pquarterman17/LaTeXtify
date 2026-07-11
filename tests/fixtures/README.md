# Test fixture corpus

Small .docx manuscripts exercising specific pipeline features. Keep each
fixture minimal (1-2 pages) and named for what it exercises:

- `zotero_cited.docx` — Zotero plugin citations (CSL JSON field codes)
- `hand_cited.docx` — typed `[N]` markers + typed reference list
- `equations.docx` — Word equation editor (OMML) content, inline + display
- `figures.docx` — embedded images with Caption-style captions
- `unsupported.docx` — text box, tracked change, floating image (preflight targets)

Fixtures are created as part of plan item 1's follow-through and each
feature item adds what it needs. Never commit real unpublished manuscripts.
