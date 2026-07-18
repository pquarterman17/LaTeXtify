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

Also implemented (plan item 21):
    merge.py      -- cross-document reference merging for supplementary
                     material: dedupes a second document's RefEntry list
                     against an already-emitted document's, reusing
                     fields.dedup_identity

Bibliography FILE intake (plan item 10, GUI Options + Formats plan) --
distinct from the field-code extractors above, these parse a whole
reference-manager export handed in as ``references_bib_path``:
    bibtex_in.py       -- .bib -> RefEntry
    csl_json_in.py     -- CSL-JSON (Zotero export) -> RefEntry
    endnote_xml_in.py  -- EndNote XML library export -> RefEntry
    nbib_in.py         -- PubMed MEDLINE .nbib -> RefEntry
    refs_import.py     -- extension dispatch across all of the above
"""
