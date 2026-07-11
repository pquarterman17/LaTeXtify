"""Equation audit tooling (plan item 23).

Word's equation editor (OMML) is the pipeline's highest-fidelity input --
plain math converts cleanly through pandoc -- but there is no way to render
a Word equation object without Word itself, so this package cannot produce a
literal side-by-side image diff. Instead it produces a *textual* side-by-side
comparison a human can scan quickly for an equation-heavy manuscript:

    equations.py -- walk the source .docx's raw OMML (``word/document.xml``)
                    for the ground-truth equation count/order, pair each one
                    with pandoc's own converted LaTeX (reusing the same
                    docx -> JSON-AST pandoc call the body pipeline uses),
                    flag any count mismatch (an equation pandoc dropped,
                    merged, or invented), write ``equations_audit.md``, and
                    optionally compile a numbered ``audit.pdf`` via Tectonic
                    -- with isolated per-equation probing so one broken
                    conversion can never take down the whole audit.

CLI surface: ``latextify equations paper.docx [--output DIR] [--pdf]``.
"""
