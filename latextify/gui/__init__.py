"""Local web GUI wrapping the conversion pipeline (plan item 19).

Deliberately buildless v1: :mod:`latextify.gui.server` is a small FastAPI
app that serves one self-contained static page
(``latextify/gui/static/index.html`` -- vanilla JS, no build step, no CDN)
and three JSON/file endpoints on top of the *existing* library layers
(:mod:`latextify.emit.project`, :mod:`latextify.compile.tectonic`,
:mod:`latextify.templates.loader`). This package contains no conversion
logic of its own -- it is purely an HTTP shell so ``latextify gui`` can
offer drag-and-drop + a journal picker + PDF preview without a frontend
build toolchain. A richer Vue/Tauri shell can replace it later without
touching anything below this package.

FastAPI/uvicorn/python-multipart are optional dependencies (the ``gui``
extra) -- ``latextify.cli``'s ``gui`` command imports this package lazily
and prints an actionable install hint if they're missing, rather than
failing every other CLI command with an ImportError.
"""
