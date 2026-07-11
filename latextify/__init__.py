"""LaTeXtify — convert Word manuscripts to journal-ready LaTeX projects.

Pipeline stages (each is a subpackage; see plans/LATEXTIFY_PLAN.md):

    ingest    -> preflight validation + pandoc conversion of the .docx body
    model     -> intermediate representation shared by all stages
    citations -> field-code / plain-text extraction to BibTeX
    figures   -> embedded-image extraction and user-file overrides
    templates -> journal registry (manifests + class files + Jinja2 templates)
    emit      -> writes the output LaTeX project (generated/manual split)
    compile   -> Tectonic PDF compilation + log parsing
    report    -> consolidated per-run conversion report
"""

__version__ = "0.1.0"
