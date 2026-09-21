LaTeXtify for Windows - editable offline repository
===================================================

This bundle is for running, editing, debugging, and testing the LaTeXtify
source code on a Windows computer that cannot reach GitHub, Astral, or PyPI.

START THE GUI
-------------
1. Extract the entire folder to a short writable path, such as
   C:\LaTeXtify-Source.
2. Double-click startup.bat.
3. The first launch creates .venv from the bundled wheelhouse. This can take
   a few minutes, but it does not use the network or require administrator
   rights. Later launches reuse that environment.

The bundle contains a portable Python 3.13 runtime, all pinned dependencies,
Pandoc, Tectonic, the warmed TeX package cache, and the complete tracked source
checkout. The GUI and command line run the files in this folder, so edits to
latextify\ take effect on the next launch.

VERIFY AND TEST
---------------
Double-click Verify-LaTeXtify.bat after transfer/extraction and before making
source edits. It checks the original shipped files against the internal
SHA-256 manifest. Intentional source edits will subsequently be reported as
changed, while .venv and normal tool caches are ignored.

The GUI's System check panel tests Pandoc, Tectonic, the TeX cache, writable
storage, offline PDF compilation, and the available EMF/WMF conversion path.
Its saved report contains no manuscript content. The synthetic acceptance
document is examples\04-combined-emf\Combined-Manuscript-with-EMF.docx.

OFFLINE DEVELOPMENT
-------------------
Run the test suite:

    .venv\Scripts\python.exe -m pytest -q -m "not network"

Run lint and type checks:

    .venv\Scripts\python.exe -m ruff check .
    .venv\Scripts\python.exe -m mypy

Run the command line directly:

    .venv\Scripts\python.exe -m latextify convert paper.docx -j revtex4-2 --pdf

NETWORK BEHAVIOR
----------------
startup.bat never downloads missing software. The bundled launch sets
LATEXTIFY_OFFLINE=1, uses the bundled Tectonic/cache, and disables Crossref
reference checks. Other GUI features remain available.

For a normal connected source checkout, startup-online.bat explicitly enables
the historical uv/dependency download setup. Do not use it on an air-gapped
computer.

EMF/WMF figures can be rasterized without additional software. Preserving them
as vectors requires an approved offline installation of LibreOffice or
Inkscape.

TROUBLESHOOTING
---------------
The startup log is latextify-startup.log beside startup.bat. Keep .runtime,
.offline-kit, and the source tree together. If endpoint security blocks
python.exe, pandoc.exe, or tectonic.exe, provide IT with SHA256SUMS.txt from
the release and request allow-listing.
