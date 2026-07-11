"""Citation extraction: docx field codes / plain text -> RefEntry list + .bib.

Citations are read directly from word/document.xml field codes, NOT from
pandoc output, because field codes carry full structured data.

Modules (plan items 7, 13; 14 still planned):
    fields.py     -- walk w:fldChar/w:instrText runs, reassemble complex
                     fields split across runs, classify by ADDIN/CITATION
                     marker, dispatch to the source-specific parser below
    zotero.py     -- ADDIN ZOTERO_ITEM CSL_CITATION {json} -> RefEntry
    mendeley.py   -- ADDIN CSL_CITATION {json} -> RefEntry
    endnote.py    -- ADDIN EN.CITE <EndNote><Cite> XML -> RefEntry
    wordnative.py -- CITATION <Tag> field + customXml/item*.xml b:Sources
                     -> RefEntry
    bib.py        -- RefEntry -> BibTeX emission, stable citation keys

Still planned (plan item 14):
    plaintext.py  -- [12] / (Smith, 2020) markers + typed reference list,
                     Crossref matching with confidence scores
    crossref.py   -- api.crossref.org client (query.bibliographic)
    reconcile.py  -- merge sources, dedupe, confidence report entries
"""
