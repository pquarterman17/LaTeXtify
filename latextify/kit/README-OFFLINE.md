# LaTeXtify — offline / air-gapped install

LaTeXtify turns a Word manuscript (`.docx`) into a journal-ready LaTeX project
and, optionally, a compiled PDF. It runs entirely on this machine — no data
ever leaves the computer.

This folder is a self-contained LaTeXtify install kit. It needs **no internet,
no compiler, and no admin rights** on this machine — only a 64-bit Python.
Everything installs into this folder; nothing else on the system is touched.

## What's inside

| File | Purpose |
|---|---|
| `install.py` | the installer — Python standard library only |
| `wheelhouse/` | LaTeXtify + every dependency as pre-built wheels (pandoc rides inside `pypandoc-binary`) |
| `tectonic/` | the Tectonic PDF-compiler binary for this platform |
| `tex-bundle-cache/` | pre-warmed TeX packages, so `--pdf` compiles offline (absent in an emit-only kit) |
| `requirements.txt` | the exact pinned versions (for IT / security review) |
| `bundle-info.json` | which OS, CPU, and Python versions this kit covers |

## Requirements

- The OS and CPU this kit was built for — see `bundle-info.json` (the folder
  name also says, e.g. `latextify-offline-windows-x64`).
- A 64-bit **Python** matching one of the versions in `bundle-info.json`
  (typically 3.10–3.14). No Python on the machine? The full installer from
  python.org runs fine without internet — a per-user install (no admin) is
  enough.

## Install

1. Extract the folder anywhere you have write access (e.g. `C:\LaTeXtify` or
   `~/latextify`). Keep the path short on Windows.
2. From inside the extracted folder run:

   ```
   Windows:      py install.py
   macOS/Linux:  python3 install.py
   ```

3. Convert a manuscript with the generated launcher:

   ```
   Windows:      LaTeXtify.bat convert paper.docx -j revtex4-2 --pdf
   macOS/Linux:  ./latextify convert paper.docx -j revtex4-2 --pdf
   ```

   The launcher points LaTeXtify at the bundled Tectonic binary and the
   pre-warmed TeX cache, so `--pdf` compiles without touching the network.
   Drop `--pdf` to emit only the LaTeX project (also fully offline). List the
   available journals with `LaTeXtify.bat journals`.

## Offline citation note

With no internet, LaTeXtify cannot reach Crossref to reconstruct a plain-text
bibliography. References that were typed as plain text are emitted **verbatim**
and flagged for you to verify; DOIs found in the typed text still hyperlink.
Citations carried as Zotero/Mendeley/EndNote field codes in the `.docx` are
resolved from the document itself and are unaffected.

## Update / uninstall

- **Update:** extract a newer kit and run its `install.py` — or copy a newer
  `wheelhouse/` over this one and re-run `install.py` here.
- **Uninstall:** delete this folder. That's all of it — the Tectonic binary and
  TeX cache live inside the folder, nothing is placed in a system location.

## Troubleshooting

- *"you are running Python X.Y, but this kit covers …"* — launch the installer
  with a covered interpreter, e.g. `py -3.13 install.py` (Windows) or
  `python3.13 install.py`.
- *`ensurepip`/venv errors on Debian/Ubuntu* — `install.py` bootstraps pip from
  the wheelhouse automatically; if it still fails, install the `python3-venv`
  OS package from your offline mirror.
- *A `--pdf` compile tries to reach the network* — the kit was built
  `--no-warm-tex` (emit-only) or the manuscript uses a package not covered by
  the warmed journals. Emit without `--pdf`, or rebuild the kit with warming on
  a connected machine.

## How this kit was made

On an internet-connected machine, from the LaTeXtify source tree:

```
uv run latextify make-kit --target current
```

That builds the LaTeXtify wheel, downloads the pinned dependency wheels for each
covered Python version, fetches the Tectonic binary, and pre-warms the TeX
cache. See `latextify/kit/build.py` in the source repository.
