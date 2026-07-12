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

The online/offline split: a **kit builder** runs on any ONLINE machine and
produces a self-sufficient folder; a **bootstrap script** consumes it on the
OFFLINE machine using nothing but the Python standard library.

```
ONLINE machine                              OFFLINE machine
──────────────                              ───────────────
latextify make-kit --target win-x64         repo zip (from GitHub)
  │                                          + kit folder (carried over)
  ├─ wheels/          platform wheels for          │
  │                   every runtime dep      python bootstrap_offline.py
  │                   (pypandoc-binary             │
  │                   wheel carries pandoc)        ├─ venv + pip --no-index
  ├─ tectonic/        target-platform binary       ├─ tectonic binary → cache
  ├─ bundle-cache/    pre-warmed TeX packages      ├─ bundle cache → cache
  │                   (platform-INDEPENDENT*)      └─ smoke test: fixture
  └─ kit-manifest.json  versions + sha256             docx → PDF, no network
```

*Assumption to VERIFY in item 2: Tectonic's package cache (~/.cache/Tectonic)
is plain TeX files, reusable across OSes — warm once, ship everywhere.

Existing pieces this builds on: `latextify/compile/tectonic.py` (cache_dir(),
find_tectonic(), download_tectonic() with platform-triple asset selection —
the kit builder reuses the triple logic for CROSS-platform download),
pypandoc-binary (pandoc ships inside the wheel — no separate binary needed),
and the Crossref client (already degrades gracefully offline: raw-text
entries with verify flags).

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
- (2026-07-11) **Python floor:** 3.10 (older lab/instrument machines).

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
   **Context:** `make-kit --target {win-x64,linux-x64,macos-arm64,current}
   --output DIR [--warm-tex/--no-warm-tex]`. Steps: (a) wheels via
   `pip download --only-binary :all: --platform <tag> --python-version 310
   -d kit/wheels .` for the project + all runtime deps (NOT dev deps) —
   fail LOUDLY listing any dep lacking a target wheel; include latextify
   itself as a built wheel (`uv build`). (b) Tectonic binary for the
   TARGET triple — generalize `download_tectonic()` to accept an explicit
   triple instead of always `platform.system()` (additive param). (c)
   FIRST verify the bundle-cache portability assumption (inspect cache
   contents for platform-specific files; document the finding), then warm
   it by compiling one minimal doc per REGISTERED journal (iterate the
   registry — not a hardcoded list of 7, per the generalize rule) and
   copy `~/.cache/Tectonic` into the kit. (d) `kit-manifest.json`:
   latextify version, target, python floor, file sha256s. Kit must be
   reproducible-ish and verifiable by the bootstrap.
   **Done when:** a kit built for the CURRENT platform installs and
   converts a fixture to PDF in a clean venv with pip `--no-index`; a
   cross-target kit builds without error and its manifest lists all deps.

3. **Offline bootstrap — `bootstrap_offline.py`**
   **Model:** Sonnet 5 · **Touches:** new repo-root `bootstrap_offline.py` (+ optional `bootstrap_offline.bat`/`.sh` wrappers)
   **Context:** STDLIB-ONLY script (the offline machine has bare Python
   3.10+ and the repo zip — nothing else): parse `--kit DIR`; validate
   manifest hashes; `python -m venv .venv`; `pip install --no-index
   --find-links kit/wheels latextify`; place tectonic binary + bundle
   cache into the exact locations `find_tectonic()`/Tectonic expect
   (reuse the path logic by importing latextify AFTER install, or
   duplicate the platformdirs logic carefully — document choice); run a
   smoke conversion of a bundled fixture WITH `--pdf` and assert no
   network (set `HTTPS_PROXY`/`HTTP_PROXY` to an unroutable address for
   the smoke test — generalizes to a reusable no-network guard). Clear,
   actionable errors at every step (wrong-platform kit, hash mismatch,
   python too old).
   **Done when:** on this Windows machine: fresh clone-zip extract + kit
   → bootstrap → `latextify convert fixture --pdf` succeeds with
   networking poisoned.

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

5. **OFFLINE.md + README section**
   **Model:** Haiku 4.5 · **Context:** the two-machine workflow start to
   finish (make-kit → carry → bootstrap → convert), per-platform notes,
   kit size expectations, LOUD callout that plain-text citation
   reconstruction offline emits verify-flagged raw entries (no Crossref),
   and the emit-only escape hatch for machines where even the kit is
   unavailable.
   **Done when:** a colleague could follow it cold.

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
