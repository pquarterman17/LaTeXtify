# LaTeXtify — Offline Portability & Python 3.10 Floor

Make LaTeXtify runnable from a repo-zip download on machines with NO
internet access and only a bare Python install (3.10+), including PDF
compilation on machines with no TeX — the quantized_matlab "unzip and
run" philosophy, adapted to a stack that carries compiled wheels and two
external binaries (pandoc, Tectonic). Each item carries a model assignment
and self-contained executor context so a cheaper model can run it standalone.

**Status:** Active
**Created:** 2026-07-11
**Updated:** 2026-07-11

---

## Context

### How the pieces fit together

REFERENCE IMPLEMENTATION (read it first): **fermiviewer's offline kit** at
`../fermiviewer/tools/offline/` — `make_bundle.py` (builder), `install.py`
(stdlib-only installer), `README-OFFLINE.md` (the user-doc shape). It is
field-tested against exactly our constraints (air-gapped lab machines, no
admin rights, no compiler, bare Python) and its `requires-python = ">=3.10"`
is the floor precedent. Do NOT reference the retired thin_film_toolkit repo;
the MATLAB repos are not the model here either.

The kit is FULLY STANDALONE — it contains the latextify wheel itself, so the
offline machine needs the kit folder only (no repo zip; the repo is for
development). Update = carry a newer kit; uninstall = delete the folder; no
admin rights; nothing outside the folder is touched except Tectonic cache
placement.

```
ONLINE machine                            OFFLINE machine (kit folder only)
──────────────                            ─────────────────────────────────
latextify make-kit --target win-x64 \
    --python-versions 3.10 ... 3.14       py install.py
  │                                            │
  ├─ install.py       stdlib-only installer    ├─ venv + pip --no-index
  ├─ wheelhouse/      latextify wheel + all    │   (pip itself bundled for
  │                   deps per covered Python  │    ensurepip-less distros)
  │                   version (pypandoc-       ├─ tectonic binary → cache
  │                   binary carries pandoc)   ├─ TeX bundle cache → cache
  ├─ requirements.txt exact pins, for IT /     ├─ writes LaTeXtify.bat /
  │                   security review          │    ./latextify launcher
  ├─ tectonic/        target-platform binary   └─ smoke: fixture docx → PDF
  ├─ tex-bundle-cache/ pre-warmed packages          with network poisoned
  │                   (platform-INDEPENDENT*)
  └─ bundle-info.json os/arch/pythons + sha256s
```

*Assumption to VERIFY in item 2: Tectonic's package cache (~/.cache/Tectonic)
is plain TeX files, reusable across OSes — warm once, ship everywhere.

Existing pieces this builds on: `latextify/compile/tectonic.py` (cache_dir(),
find_tectonic(), download_tectonic() with platform-triple asset selection —
the kit builder reuses the triple logic for CROSS-platform download),
pypandoc-binary (pandoc ships inside the wheel — no separate binary needed),
and the Crossref client (already degrades gracefully offline: raw-text
entries with verify flags). Known fermiviewer subtleties to inherit: plain
`pip download --python-version` is NOT sufficient alone (see
make_bundle.py's module docstring — follow its per-version download loop),
and pip itself is bundled in the wheelhouse to cover Debian/Ubuntu's
missing-ensurepip gap.

### Model routing

| Model | Used for | Items |
|---|---|---|
| Haiku 4.5 | Mechanical, well-specified | 1, 5, 6 |
| Sonnet 5 | Standard implementation with fiddly externals | 2, 3, 4, 7 |

### Executor protocol

Same as the archived main plan (plans/archive/LATEXTIFY_PLAN.md Context):
worktree branch, uv sync, tests alongside code, `uv run pytest` +
`uv run ruff check .` green, conventional commits, close out per
plan-hygiene. Plus the standing rule: fix/build for the CLASS, not the
instance — tests must cover members beyond the motivating example.

### Resolved decisions

- (2026-07-11) **Acquisition model:** repo zip + separately-built offline
  kit (`latextify make-kit`), NOT vendored-in-repo binaries, NOT (for now)
  single-file executables.
- (2026-07-11) **Offline target platforms:** Windows x64, Linux x64, macOS
  arm64 — kit builder must cross-target all three from any host.
- (2026-07-11) **LaTeX-less machines:** pre-warmed Tectonic bundle cache in
  the kit is THE strategy. No system-TeX fallback engine for now
  (emit-only remains free as a documented workaround).
- (2026-07-11) **Python floor:** 3.10 (older lab/instrument machines;
  matches fermiviewer's floor).
- (2026-07-11) **Kit anatomy = fermiviewer's** (`tools/offline/` there):
  fully standalone kit containing the latextify wheel (no repo zip needed
  offline), multi-Python-version wheelhouse (3.10–3.14), in-kit stdlib
  `install.py`, pinned `requirements.txt` for IT review, `bundle-info.json`,
  installer-written launchers. LaTeXtify adds `tectonic/` +
  `tex-bundle-cache/` on top. thin_film_toolkit is retired — never
  reference it.

### Known risks

- Cross-platform `pip download` needs exact tags (`win_amd64`,
  `manylinux2014_x86_64`, `macosx_11_0_arm64`) + `--only-binary :all:`;
  any dep without a wheel for a target breaks that kit — item 2 must fail
  loudly per-dep, not silently produce an incomplete kit.
- Bundle-cache portability across OSes is assumed, not proven (item 2
  verifies first). Fallback if wrong: warm per-platform in CI (item 7).
- Kit size ~200–300 MB/platform (pypandoc-binary ~40 MB, Tectonic ~30 MB,
  bundle ~100–200 MB, wheels ~50 MB). Acceptable for sneakernet; item 6
  offers trimming.
- Offline machines also can't reach Crossref: plain-text citation
  reconstruction emits raw-text entries, ALL flagged verify. Document
  loudly (item 5); a future "reconcile online later" command is out of
  scope here.

### Dependency map

- Item 1 independent — do first (its CI matrix change gates everything)
- Item 2 before 3 (bootstrap consumes the kit format), 3 before 4
- Items 5, 6 after 3; item 7 after 4

---

## Tier 1 — High Impact

1. **Python 3.10 floor**
   **Model:** Haiku 4.5 · **Touches:** `latextify/model/{compile,figure,preflight}.py`, `pyproject.toml`, `.github/workflows/ci.yml`
   **Context:** Only real 3.11-ism is `enum.StrEnum` in the three model
   files — replace with `class X(str, Enum)` (behavior-compatible for our
   uses; verify str-formatting call sites: report renderer sorts/prints
   severity values). `requires-python = ">=3.10"`, add 3.10 classifier,
   ruff `target-version = "py310"` (then fix anything ruff flags as
   3.10-incompatible), CI matrix gains "3.10". Audit for stragglers:
   grep `tomllib|datetime.UTC|typing.Self|StrEnum|except\*`.
   **Done when:** full suite green on 3.10 locally (`uv run --python 3.10
   pytest`) and in CI matrix.

2. **Offline kit builder — `latextify make-kit`**
   **Model:** Sonnet 5 · **Touches:** new `latextify/kit/` package, `cli.py` (one command)
   **Context:** MIRROR `../fermiviewer/tools/offline/make_bundle.py` — read
   it end to end first; it solved the traps already. `make-kit --target
   {win-x64,linux-x64,macos-arm64,current} --python-versions 3.10..3.14
   --output DIR [--warm-tex/--no-warm-tex]`. Kit contents (fermiviewer
   anatomy + our TeX layer): (a) `wheelhouse/` — latextify wheel
   (`uv build`) + all runtime deps for EVERY covered Python version
   (follow make_bundle.py's per-version `pip download` loop; its module
   docstring explains why `--python-version` alone is insufficient), plus
   pip itself (Debian ensurepip gap); fail LOUDLY naming any dep lacking
   a wheel for the target. (b) `requirements.txt` — exact pins, for IT/
   security review. (c) `install.py` — copied from a template in the
   package (see item 3). (d) `tectonic/` — TARGET-triple binary
   (generalize `download_tectonic()` additively to accept an explicit
   triple). (e) `tex-bundle-cache/` — FIRST verify the cross-OS
   portability assumption (inspect for platform-specific files; document
   finding), then warm by compiling one minimal doc per REGISTERED
   journal (iterate the registry, not a hardcoded list). (f)
   `bundle-info.json` — os/arch/python versions covered, latextify
   version, sha256s; kit folder named `latextify-offline-<os>-<arch>`.
   **Done when:** a current-platform kit installs + converts a fixture to
   PDF in a clean venv with `--no-index`; a cross-target kit builds
   without error and its manifest lists everything.

3. **Offline installer — `install.py` (ships INSIDE the kit)**
   **Model:** Sonnet 5 · **Touches:** new `latextify/kit/install_template.py` (emitted into kits by item 2)
   **Context:** MIRROR `../fermiviewer/tools/offline/install.py` — stdlib
   only, no admin, everything into the kit folder. Flow: check the
   running interpreter against bundle-info.json's covered versions (clear
   "run me with py -3.13" style errors); validate sha256s; `python -m
   venv`; bootstrap pip from the wheelhouse when ensurepip is missing;
   `pip install --no-index --find-links wheelhouse latextify`; place the
   tectonic binary + TeX bundle cache into the exact paths
   `find_tectonic()`/Tectonic expect (import latextify AFTER install to
   reuse its path logic); write `LaTeXtify.bat` / `./latextify` launcher
   wrappers; finish with a smoke conversion of a bundled fixture WITH
   `--pdf` under poisoned proxies (`HTTPS_PROXY=http://127.0.0.1:9`) so
   success PROVES offline operation. Uninstall = delete folder (document
   the one exception: the Tectonic cache location, and offer
   `--cache-here` to keep even that inside the folder if feasible).
   **Done when:** on this Windows machine: kit folder alone (no repo) →
   `py install.py` → launcher converts a fixture to PDF with networking
   poisoned.

4. **Offline CI verification**
   **Model:** Sonnet 5 · **Touches:** `.github/workflows/ci.yml` (new job)
   **Context:** One job proving the whole story end to end on ubuntu:
   build kit (current platform) → fresh venv → bootstrap with proxies
   poisoned (`HTTPS_PROXY=http://127.0.0.1:9` etc.) → fixture convert
   `--pdf` → assert PDF exists. Cache the kit build's downloads where
   sensible. This is the regression gate that keeps offline support from
   silently rotting.
   **Done when:** job green in CI and fails if someone adds a runtime
   network call to the convert path.

## Tier 2 — Medium Impact

5. **README-OFFLINE.md (in-kit) + repo README section**
   **Model:** Haiku 4.5 · **Context:** MIRROR the structure and tone of
   `../fermiviewer/tools/offline/README-OFFLINE.md` (what's-inside table,
   requirements incl. the "python.org installer works offline, per-user,
   no admin" note, install, update = newer kit / uninstall = delete
   folder, troubleshooting, how-this-was-made). Add the LaTeXtify
   specifics: kit size expectations, LOUD callout that plain-text
   citation reconstruction offline emits verify-flagged raw entries (no
   Crossref; DOIs found in the typed text still hyperlink), and the
   emit-only escape hatch.
   **Done when:** a colleague could follow it cold on an air-gapped PC.

6. **Kit trimming options**
   **Model:** Haiku 4.5 · **Context:** `--no-warm-tex` (emit-only kits,
   ~70 MB), `--journals a,b` to warm only selected journals' bundles,
   skip GUI extra wheels by default with `--with-gui` opt-in.
   **Done when:** each flag changes kit contents + manifest accordingly,
   with tests on the manifest.

7. **Release kits from CI**
   **Model:** Sonnet 5 · **Context:** on version tag, CI builds kits for
   all three targets (bundle warming runs once on ubuntu and is shared —
   or per-platform if item 2's portability verification said otherwise)
   and attaches them as release artifacts alongside a source zip, so
   "download from the releases page" needs no online build machine at all.
   **Done when:** a tagged release carries three kit artifacts + checksums.

## Completed

(none yet)
