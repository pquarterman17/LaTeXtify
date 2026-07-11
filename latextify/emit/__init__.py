"""Project emitter: write the output LaTeX project tree.

Output contract (plan item 5) — the generated/manual split:
    output/<journal>/
        main.tex           -- USER-OWNED: written only if absent, never
                              overwritten; \\input's the generated files
        generated/
            preamble.tex   -- regenerated every run from the journal template
            metadata.tex   -- regenerated every run (title/authors/abstract)
            body.tex       -- regenerated every run from the converted body
        figures/           -- resolved figure files
        references.bib     -- regenerated every run
        report.md          -- per-run conversion report

Planned modules:
    project.py  -- tree writing, main.tex write-once logic
    metadata.py -- Author/Affiliation IR -> per-journal macro emission via
                   the journal's metadata.tex.j2
"""
