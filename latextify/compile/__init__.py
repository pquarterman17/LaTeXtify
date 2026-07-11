"""PDF compilation via Tectonic (self-contained TeX engine).

Tectonic downloads packages on demand — no MiKTeX/TeX Live required.
Journal classes missing from the Tectonic bundle are vendored into the
output tree by the template registry (templates/<journal>/vendor/).

Planned modules (plan item 6):
    tectonic.py -- binary detection/installation, `tectonic -X compile`
                   invocation with the output tree as workdir
    logs.py     -- parse the TeX log into structured errors/warnings for
                   the report (never show the user raw TeX spew)
"""
