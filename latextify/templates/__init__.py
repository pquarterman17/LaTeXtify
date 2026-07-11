"""Journal template registry — journals are DATA, not code.

Each journal lives at journals/<name>/ containing:
    manifest.yaml   -- document class + options, required packages,
                       bibliography style(s), citation modes offered
                       (numeric/authoryear), author/affiliation macro
                       scheme, figure environment conventions
    preamble.tex.j2 -- Jinja2 preamble template
    metadata.tex.j2 -- Jinja2 title/author/abstract block template
    vendor/         -- class/style files not in the Tectonic bundle (optional)

Adding a journal means adding a folder — never editing converter code.

Planned modules (plan item 4):
    loader.py -- discover journals/, validate manifests against the schema,
                 raise clear errors on bad manifests
"""
