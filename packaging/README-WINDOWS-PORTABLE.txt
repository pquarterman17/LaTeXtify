LaTeXtify for Windows - portable, offline edition
=================================================

START
-----
1. Extract the entire LaTeXtify-Windows-Portable folder to a short,
   writable location such as C:\LaTeXtify.
2. Double-click LaTeXtify.exe.
3. Keep LaTeXtify.exe and the _internal, tectonic, and tex-bundle-cache
   folders together. Do not run the program from inside the ZIP file.

There is nothing to install. This edition carries its own Python runtime,
Pandoc, Tectonic, GUI dependencies, and TeX package cache. It does not need
uv, pip, an installed copy of Python, administrator rights, GitHub, Astral,
or PyPI. Crossref reference checking is disabled because it needs internet;
the other GUI options continue to work.

VERIFY AND TEST
---------------
Double-click Verify-LaTeXtify.bat after transferring or extracting the ZIP.
It checks every required shipped file against the package's internal SHA-256
manifest and reports missing or changed files.

The GUI's System check panel tests the conversion tools and writes a
content-free report suitable for IT. The sample folder contains a synthetic
combined main+supplement Word document with two embedded EMF figures; use it
to test the exact offline workflow without exposing research data.

The interface is private to this computer and opens in your normal browser at
an address beginning with http://127.0.0.1/. Close the browser tab to stop it.

TROUBLESHOOTING
---------------
If startup fails, look for latextify-startup.log beside LaTeXtify.exe. If this
folder is read-only, the log is written under:

    %LOCALAPPDATA%\LaTeXtify\latextify-startup.log

A managed work computer may block unapproved executables through AppLocker or
endpoint security. In that case, give IT the release's SHA256SUMS.txt and ask
them to allow the published LaTeXtify.exe, pandoc.exe, and tectonic.exe files.

EMF/WMF figures can be rasterized without additional software. Preserving them
as vectors requires an approved offline installation of LibreOffice or
Inkscape.
