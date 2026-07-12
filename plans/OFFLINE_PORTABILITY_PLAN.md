# LaTeXtify — Offline Portability & Python 3.10 Floor

Make LaTeXtify runnable from a repo-zip download on machines with NO
internet access and only a bare Python install (3.10+), including PDF
compilation on machines with no TeX — the quantized_matlab "unzip and
run" philosophy, adapted to a stack that carries compiled wheels and two
external binaries (pandoc, Tectonic). Each item carries a model assignment
and self-contained executor context so a cheaper model can run it standalone.

**Status:** Active
**Created:** 2026-07-11
**Updated:** 2026-07-12

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

## Tier 3 — Nice-to-Have

8. **Usage example scripts** (added 2026-07-12, user request)
   **Model:** Sonnet 5 · **Touches:** new `examples/` tree, README link.
   **Context:** Three runnable, self-contained examples covering the input
   shapes a real user hits, each with a fixture-generator (python-docx, as
   the tests do — no committed binaries) + a run script + an expected-output
   note. The three scenarios:
   (a) **all-embedded** — one `.docx` with figures embedded and citations
       inline; a single `latextify convert paper.docx -j revtex4-2 --pdf`.
   (b) **Word + separate figures** — a `.docx` plus a sibling `figures/`
       folder (folder-convention overrides) and/or a `figures.yaml` manifest;
       shows the figure-override tiers.
   (c) **fully multi-part** — a main `.docx` + a separate supplementary
       `.docx` (`--supplement`) + externally-managed references (citations
       carried as Zotero/Mendeley/EndNote field codes in the docx, reconciled
       via Crossref; document how a reference-manager library feeds in).
   VERIFY which of these the tool supports end-to-end today (esp. the
   reference-manager path in (c)); build examples on the supported paths and
   record any gap as a new item rather than faking it.
   **Done when:** each example runs from a clean checkout to a PDF (or a clear
   "needs Tectonic/network" note) and is linked from the README.

## Completed

- ~~**Item 4 — Offline CI verification**~~ (2026-07-12) — new `offline-kit` job in
  `.github/workflows/ci.yml`: builds a current-platform kit (network ON), then
  installs it and compiles a fixture `.docx` to PDF with HTTP(S)/ALL proxies
  poisoned (`127.0.0.1:9`). Asserts `main.pdf` exists — so a stray runtime network
  call on the convert→compile path fails the job. Fixture is generated in-CI with
  the dev-group `python-docx` (no committed binary); kept to one Python (3.13) and
  one journal (revtex4-2) for speed. Reuses the integration job's Tectonic cache +
  authenticated pre-fetch pattern.
- ~~**Item 7 — Release kits from CI**~~ (2026-07-12) — new
  `.github/workflows/release.yml`: on a `v*` tag, builds all three kits and
  attaches them + a `git archive` source zip + `SHA256SUMS.txt` to a GitHub
  release via the preinstalled `gh` CLI (no third-party action). Warms the TeX
  cache ONCE on the native Linux build and copies it into the cross-built
  Windows/macOS kits (the item-2 portability finding makes this sound: the cache
  is host-independent TeX sources; the target regenerates its format offline),
  patching each cross kit's `bundle-info.json` so the manifest stays honest.
- ~~**Item 2 — offline kit builder `latextify make-kit`**~~ (2026-07-12) — new
  `latextify/kit/` package + CLI command builds
  `latextify-offline-<os>-<arch>/` with wheelhouse (latextify wheel + all deps
  per covered Python + pip), `requirements.txt`, `install.py`, target-triple
  `tectonic/` binary, warmed `tex-bundle-cache/`, and `bundle-info.json`. Cross
  targets fetch wheels via pip `--platform`/`--only-binary=:all:` (fail loud on a
  dep with no target wheel); the Tectonic download was generalized additively
  (`download_tectonic_release(triple, binary, dir)`). **Verified**: a
  current-platform (win-x64) kit built, installed in a clean venv `--no-index`,
  and converted a fixture to PDF with the network poisoned. **Cross-OS cache
  finding**: the warmed cache ships TeX *sources* only — the host/arch-specific
  engine `formats/` dump is stripped, and the target regenerates the format
  locally + offline from the sources (proven: format regenerated under poisoned
  proxies). Warming uses a comprehensive font/size/weight/math warm-up doc
  (a trivial body under-warms — real 9pt abstracts / 12pt-bold headings pull
  font files a one-liner never loads) and runs in a short-path temp cache to
  dodge Windows MAX_PATH during `.fmt` generation.
- ~~**Item 3 — in-kit `install.py` template**~~ (2026-07-12) —
  `latextify/kit/install_template.py`, stdlib-only (test-enforced: no
  latextify/third-party imports), copied verbatim into every kit. Creates a
  `.venv`, bootstraps pip from the wheelhouse on ensurepip-less distros, installs
  `--no-index`, verifies the import, and writes `LaTeXtify.bat` / `./latextify`
  launchers that put the in-kit Tectonic on PATH and point `TECTONIC_CACHE_DIR`
  at the warmed cache — so uninstall = delete the folder (nothing placed in a
  system location; simpler than the plan's cache-copy approach).
- ~~**Item 5 — README-OFFLINE.md**~~ (2026-07-12) — `latextify/kit/README-OFFLINE.md`,
  shipped in each kit: what's-inside table, requirements, install/convert,
  update/uninstall, troubleshooting, how-made, plus the loud offline-citation
  callout (verify-flagged raw entries; DOIs still hyperlink) and the emit-only
  escape hatch.
- ~~**Item 6 — kit trimming flags**~~ (2026-07-12) — `--no-warm-tex` (emit-only,
  omits `tex-bundle-cache/`), `--journals a,b` (warm a subset), `--with-gui`
  (bundle the GUI extra). Each is reflected in `bundle-info.json`; manifest
  effects are unit-tested (`tests/test_kit_build.py`).
- ~~**Item 1 — Python 3.10 floor**~~ (2026-07-12) — the only 3.11-ism was
  `enum.StrEnum` (three model files). Rather than `class X(str, Enum)` (which
  changes `__str__`/`__format__` to print `Class.MEMBER`), added a version-gated
  shim `latextify/model/_compat.py`: stdlib `StrEnum` on 3.11+, a
  behaviour-identical backport on 3.10. `requires-python = ">=3.10"`, 3.10 + 3.14
  classifiers, ruff `target-version = "py310"`, CI matrix gains 3.10. Full unit
  suite (726) green under `uv run --python 3.10`; new `tests/test_compat_strenum.py`
  locks in the semantics the report renderer depends on.
