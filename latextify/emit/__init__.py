"""Project emitter: write the output LaTeX project tree.

Output contract (plan item 5) — the generated/manual split:
    output/<journal>/
        main.tex           -- USER-OWNED: written only if absent, never
                              overwritten; \\input's the generated files
        generated/
            preamble.tex   -- regenerated every run from the journal template
            metadata.tex   -- regenerated every run (title/authors/abstract)
            body.tex       -- regenerated every run from the converted body
            bibliography.tex -- regenerated every run: the \\bibliography line
                              when references exist, a comment when none do, so
                              citation-free manuscripts compile (plan item 26)
        figures/           -- resolved figure files (main fig<N>.<ext> and,
                              when a --supplement was given, SI figS<N>.<ext>)
        references.bib     -- regenerated every run (shared by main.tex and
                              supplement.tex; deduped across both, item 21)
        report.md          -- per-run conversion report

    output/<journal>/ (only when emit_project's supplement_docx_path is
    given -- plan item 21):
        supplement.tex     -- USER-OWNED: written only if absent, never
                              overwritten; \\input's the generated/
                              supplement_*.tex files
        generated/
            supplement_preamble.tex    -- the journal preamble (reused
                                          verbatim) + S1/S2/... numbering
                                          \\renewcommand's
            supplement_metadata.tex    -- title only ("Supplementary
                                          Material: <main title>"), derived
                                          from the main document's Meta --
                                          no metadata guessing on the SI docx
            supplement_body.tex        -- regenerated every run
            supplement_bibliography.tex -- same generated-\\bibliography-line
                                          mechanism as bibliography.tex

Planned modules:
    project.py  -- tree writing, main.tex write-once logic
    metadata.py -- Author/Affiliation IR -> per-journal macro emission via
                   the journal's metadata.tex.j2
"""
