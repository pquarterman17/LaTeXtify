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
**Updated:** 2026-07-12

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

1. **Prove the win-x64 kit installs and runs on Python 3.10 offline** — the exact
   target scenario, which CI never exercises (its offline gate is Linux/3.13).
   - [ ] Build a win-x64 kit natively on Windows (`make-kit --target current`)
   - [ ] Install it with a Python 3.10 interpreter, network poisoned
   - [ ] Confirm emit-only (docx → LaTeX) works with no network
   - [ ] Confirm `--pdf` works (Tectonic + warmed cache) with no network

2. **Correct the advertised Python floor** — README badge says `3.11+` but the
   package is `>=3.10` and proven on 3.10.
   - [ ] Change the badge to `3.10+`

3. **Document the executable-lockdown reality + emit-only fallback** in
   `README-OFFLINE.md` — be honest that pandoc is always required and Tectonic is
   required for `--pdf`, and give the emit-only path when a binary is blocked.
   - [ ] Add a short "if a binary is blocked" section (soft vs hard lockdown)
   - [ ] State that emit-only avoids Tectonic but still needs pandoc

## Tier 2 — Medium Impact

4. **Add a CI gate that install-tests a kit on Python 3.10** so a future
   dependency bump can't silently break the floor (today only 3.13/Linux is
   gated).

5. **Recommend/document native Windows kit builds** over the Linux cross-build —
   native build warms the TeX cache on Windows (the cross path ships
   `--no-warm-tex` and copies a Linux-warmed cache), which is more robust.

6. **Publish to PyPI** — the "normal version" for online users: one workflow,
   same wheel, trusted publishing. Enables `pip install latextify`.

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

- ~~**3.10 floor validated**~~ (2026-07-12) — full offline suite (966 tests)
  passes under Python 3.10; no 3.11+ features in the codebase. Recorded here as
  the evidence behind the "floor at 3.10" decision; the remaining 3.10 work is
  item 4 (lock it in CI).
