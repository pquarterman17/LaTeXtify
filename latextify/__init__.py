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

from importlib.metadata import PackageNotFoundError, version

# Derived from the installed distribution so pyproject.toml is the only place
# the version is written. A hardcoded copy here went stale for all of v0.2.0:
# the offline kit's install check (kit/install_template.py) reported "0.1.0".
try:
    __version__ = version("latextify")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
