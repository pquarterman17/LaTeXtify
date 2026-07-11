"""Citation extraction: docx field codes / plain text -> RefEntry list + .bib.

Citations are read directly from word/document.xml field codes, NOT from
pandoc output, because field codes carry full structured data.

Planned modules (plan items 7, 13, 14):
    fields.py     -- walk w:fldChar/w:instrText runs, reassemble complex
                     fields split across runs, classify by ADDIN marker
    zotero.py     -- ADDIN ZOTERO_ITEM CSL_CITATION {json} -> RefEntry
    mendeley.py   -- ADDIN CSL_CITATION {json} -> RefEntry
    endnote.py    -- ADDIN EN.CITE <EndNote><Cite> XML -> RefEntry
    wordnative.py -- customXml/item*.xml b:Sources -> RefEntry
    plaintext.py  -- [12] / (Smith, 2020) markers + typed reference list,
                     Crossref matching with confidence scores
    crossref.py   -- api.crossref.org client (query.bibliographic)
    bib.py        -- RefEntry -> BibTeX emission, stable citation keys
    reconcile.py  -- merge sources, dedupe, confidence report entries
"""
