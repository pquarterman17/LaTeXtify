# LaTeXtify — Offline Install Validation & Distribution

Make the offline install *trustworthy and turnkey* for the concrete real-world
case — a locked-down Windows 10 machine with no internet, no admin, and only a
pre-existing Python (3.10+) — and give unconstrained users a dead-simple online
path, without forking the product. The offline kit itself already exists (see
the archived `OFFLINE_PORTABILITY_PLAN.md`); this plan closes the gap between
"declared to work" and "proven to work" for that machine, and decides the
distribution channels.

**Status:** Active
**Created:** 2026-07-12
**Updated:** 2026-07-18 — Tiers 1 + 2 fully shipped (items 1–6, 9); only Tier 3
7/8 remain (both gated on need/audience). PyPI publish is wired but needs the
two owner steps before `pip install latextify` is live.

---

## Context

### How the pieces fit together

The offline story is one artifact — the LaTeXtify wheel — packaged two ways, not
two products:

- **Offline kit** (`latextify make-kit`, `latextify/kit/build.py`): a
  self-contained folder with `wheelhouse/` (LaTeXtify + every dependency as
  wheels for each covered Python version), `tectonic/` (PDF binary),
  `tex-bundle-cache/` (pre-warmed TeX packages so `--pdf` compiles offline),
  and a stdlib-only `install.py`. Installs with no internet and no admin.
  `release.yml` already builds win/linux/mac kits on a version tag and attaches
  them to a GitHub Release with checksums.
- **Online channel** (does not exist yet): `pip install latextify` from PyPI —
  the same wheel, for users who have internet.

### The load-bearing constraint (read this first)

LaTeXtify is **not pure Python**. Its pipeline shells out to two native
executables:

- **pandoc** (bundled in `pypandoc-binary`) does *all* of docx → LaTeX, and is
  required even for emit-only (there is no pure-Python ingest path).
- **Tectonic** does LaTeX → PDF (only needed for `--pdf`).

Consequence for a locked-down box:

- **Soft lockdown** (no admin / can't install an app, but can launch a binary
  from user-space): the kit works — the bundled binaries are just files. Most
  "no admin" corporate machines are here.
- **Hard lockdown** (AppLocker / allow-list refuses any non-approved binary):
  Python's call to pandoc is blocked too, so LaTeXtify **cannot run at all**.
  This is architectural, not a packaging gap — no kit fixes it. Emit-only still
  needs pandoc; only `--pdf` is what additionally needs Tectonic.

### Data / control flow

```
ONLINE build machine (Windows, has internet)     TARGET (offline, has Python 3.10+)
────────────────────────────────────────────     ──────────────────────────────────
latextify make-kit --target win-x64          →    copy folder via USB
  wheelhouse/ + pandoc + tectonic + TeX cache      py install.py   (venv + pip --no-index)
                                                    LaTeXtify.bat convert paper.docx -j ... [--pdf]
                                                      docx --pandoc--> LaTeX  [--tectonic--> PDF]
```

### Resolved decisions

- (2026-07-12) **Do not bundle a Python runtime.** Assume Python is present on
  the target; if it isn't, the case is out of scope ("we're hosed"). This keeps
  the existing multi-version wheelhouse (3.10–3.14) rather than collapsing to a
  single bundled interpreter.
- (2026-07-12) **Floor at Python 3.10**, up through current. Validated: the full
  offline suite (966 tests) passes on 3.10; `requires-python = ">=3.10"` and no
  3.11+/3.12+-only features are used.
- (2026-07-12) **One wheel, two channels** (PyPI + offline kit). No bespoke
  "online installer", no forked codebase.
- (2026-07-12) **No single-.exe freeze (PyInstaller/Nuitka).** It does not
  remove the pandoc/Tectonic/TeX dependency, and unsigned exes trip the very
  AV/allow-listing found on locked-down machines — it trades a solved problem
  for an unsolved one. Reconsider only with code-signing in hand.

### Dependency map

- Items 1, 2, 3 are independent and can land in any order.
- Item 4 (CI 3.10 kit gate) depends conceptually on item 1 (prove it manually
  first, then automate).
- Item 6 (PyPI) is independent of the offline items entirely.
- Items 7, 8 are gated on the Owner gates below — do not start without answers.

---

## Tier 1 — High Impact

*(All shipped — see Completed.)*

## Tier 2 — Medium Impact

*(All shipped — see Completed.)*

## Tier 3 — Nice-to-Have

7. **Evaluate a full Tectonic bundle file vs. the warmed cache.** The warmed
   cache only covers packages the warmed journals used, so an uncovered package
   fails offline. A full local bundle makes *any* LaTeX compile offline and
   deletes the warming logic — more robust, fixed size, less maintenance.
   Evaluate sizes/flags before committing.

8. **One-click self-extractor / installer** (extract + run `install.py` + drop a
   shortcut) — only if a non-technical or broad audience materializes. Not for
   the current known, semi-technical use case.

---

## Owner gates

- **Executable policy on the target machine.** Can the locked-down box launch a
  bundled `.exe` (pandoc/Tectonic) that Python invokes from user-space, or does
  it hard-block all non-approved binaries? This decides whether `--pdf`, emit-
  only, or *nothing* works. Determinable in ~2 min on the machine (drop a small
  portable exe in the user profile and try to run it). Blocks nothing in Tier 1
  code, but decides expectations and whether Tier 3 item 7 matters.
- **Audience scope.** A handful of known machines vs. a broad public offering.
  Broad audience justifies items 6 and 8; a few known machines does not.

---

## Completed

- ~~**#9 Make `make-kit --zip` robust to long build paths (Windows MAX_PATH)**~~
  (2026-07-18) — `_zip_kit` replaced `shutil.make_archive` with an explicit
  `zipfile` walk routed through the existing `_long_path()` `\\?\` helper (the
  same escape hatch `_copy_portable_cache` already used), preserving the exact
  archive layout (regression-tested against `shutil.make_archive` output).
  Verified against the real failing scenario: a warm-tex kit `--zip` to a deep
  scratchpad path now produces a valid 87 MB zip (408 members incl.
  `tex-bundle-cache`) — it threw `FileNotFoundError` before. 2 tests.
- ~~**#6 Publish to PyPI**~~ (2026-07-18) — machinery in place:
  `.github/workflows/publish.yml` builds sdist+wheel (`uv build` + `twine
  check`) and publishes via **trusted publishing / OIDC** (`id-token: write`,
  environment `pypi`), triggered ONLY on `release: published` (no push/PR/tag —
  no accidental publish). pyproject PyPI metadata rounded out (classifiers,
  Homepage; License left as the PEP 639 SPDX field, verified by `twine check`).
  actionlint-clean. **Two OWNER steps remain before `pip install latextify`
  works:** (1) register the pending trusted publisher on pypi.org (project
  `latextify`, repo `LaTeXtify`, workflow `publish.yml`, env `pypi`);
  (2) publish a GitHub Release to fire it.
- ~~**#5 Recommend/document native Windows kit builds**~~ (2026-07-18) — comment
  block in `release.yml` above the cross-build step explaining native Windows
  builds (warming their own TeX cache) are preferred over the Linux cross-build
  (`--no-warm-tex` + copied Linux-warmed cache) once a native runner exists.
- ~~**#4 CI gate: install-test a kit on Python 3.10**~~ (2026-07-18) — new
  `ci.yml` job `offline-kit-py310-windows` (windows-latest): builds an emit-only
  3.10 kit, installs it under a real 3.10 interpreter and converts a fixture,
  both with the network poisoned (dead proxy), asserting `main.tex`. Catches a
  future dep bump silently breaking the 3.10 floor (CI previously gated only
  3.13/Linux).
- ~~**#3 Document the executable-lockdown reality + emit-only fallback**~~
  (2026-07-18) — new `## If a binary is blocked` section in `README-OFFLINE.md`:
  pandoc is required for every conversion (even emit-only), Tectonic only for
  `--pdf`; soft lockdown → kit works, hard lockdown (AppLocker) → pandoc blocked,
  can't run at all (architectural); drop `--pdf` as the fallback when only
  Tectonic/network is unavailable.
- ~~**#1 Prove the win-x64 kit installs and runs on Python 3.10 offline**~~
  (2026-07-18) — **the exact target scenario, now proven end-to-end.** Ran the
  sibling `offline-testbed` Windows Sandbox (`Test-OfflineInstall.ps1 -Python
  3.10 -ProjectProfile latextify -Fixture clean.docx -SkipWebView2`) against a
  natively-built win-x64 warm-tex kit. **Overall: PASS** on a genuinely
  network-poisoned guest — all 8 steps green: `offline-check` (dns=False
  ping=False), install Python 3.10, extract, `install.py` (offline
  `pip --no-index`), `import latextify 0.1.0`, launcher written, `convert
  clean.docx → main.tex`, and `compile-pdf main.pdf` (12218 bytes, bundled
  Tectonic, no network). Direct dev-machine tests on py3.14 corroborated
  earlier. The offline-testbed gaps that had blocked this (no latextify
  profile, Python matrix ≤3.13, no `-Fixture`) were fixed by the harness owner
  after the findings file was left; 3.10–3.14 installers are all cached now.
- ~~**#2 Correct the advertised Python floor**~~ (2026-07-17) — README badge
  changed to `3.10+` in commit dbf94fd (matches `requires-python` and the CI
  matrix). Closed retroactively 2026-07-18 during a reconciliation pass.
- ~~**3.10 floor validated**~~ (2026-07-12) — full offline suite (966 tests)
  passes under Python 3.10; no 3.11+ features in the codebase. Recorded here as
  the evidence behind the "floor at 3.10" decision; the remaining 3.10 work is
  item 4 (lock it in CI).
