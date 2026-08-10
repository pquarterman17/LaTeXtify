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

Modules:
    project.py              -- tree writing, main.tex write-once logic; the
                                pipeline entry point (emit_project)
    supplement.py           -- the SI document (plan item 21): emit_supplement
                                runs the same preflight/pandoc/figures/citations
                                pipeline a second time, S-prefixed and merged
                                into the main document's output tree
    citation_resolution.py  -- plain-text citation reconstruction (item 14,
                                the no-field-codes fallback) and the opt-in
                                online reference validation (--check-references)
    bibliography.py         -- the \\bibliography-vs-comment inclusion contract
                                (item 26) shared by main.tex and supplement.tex,
                                plus the pre-item-26 main.tex migration warning
    metadata.py             -- Author/Affiliation IR -> per-journal macro emission
                                via the journal's metadata.tex.j2
    figures_copy.py         -- what lands in figures/: per-figure convert-or-copy,
                                wide-float sizing, stale-file pruning
"""
