# LaTeXtify — Repo Health & Release Safety

Cross-cutting maintainability and release-safety gaps found during codebase audit (2026-08-10): type-checking deaf spot, version-agreement drift risk, formatter declaration-but-unenforced, module size ceiling approaching, and toolchain version fragility.

**Status:** Active
**Created:** 2026-08-10
**Updated:** 2026-08-10 (item 6 booked: convert.py at 495/500)

---

## Context

### How the pieces fit together

LaTeXtify is ~20k lines of Python with thorough type annotations that nothing
verifies, and two files declaring the project version with nothing asserting
they agree.

Verified against the repo on 2026-08-10, so a future session does not have to
re-derive it:

- **No type checking exists** — no `mypy` config in `pyproject.toml`, no
  type-check step in any workflow.
- **The version lives in exactly two files.** The real `v0.2.0` release commit
  (`2b3915a`) touched `pyproject.toml` and `uv.lock`, nothing else. A stale
  `uv.lock` fails a `--locked` build, so the pair must not drift — but the
  exposure here is small and the guard is correspondingly cheap.
- **There is no release checklist document** (no `RELEASE.md`). The release
  procedure lives only in git history. That is itself a gap: the guard in
  item 2 is what makes a checklist unnecessary rather than merely absent.
- **`latextify/gui/server.py` is 916 lines against a 921-line pin** — five
  lines of headroom, effectively frozen. The 2026-08-10 privacy GUI work
  routed its new routes into `gui/uploads_routes.py` specifically to avoid
  spending them.
- **CI hardcodes Python versions** with no `.python-version` to derive from.

### Dependency map

- **Type checking (item 1)** is independent; blocks nothing yet, depends on owner decision (strategy: narrow start or full coverage).
- **Version agreement (item 2)** is independent.
- **Ruff format (item 3)** is independent; depends on owner decision (adopt or drop).
- **Server.py decomposition (item 4)** is needed whenever the next GUI feature lands; planned deliberately rather than under deadline.
- **Python version file (item 5)** is independent, nice-to-have.
- **convert.py split (item 6)** is independent of everything else and is the
  same failure mode as item 4 one directory over.

---

## Tier 1 — High Impact

1. **Adopt type checking** — the codebase is thoroughly annotated but NOTHING verifies any of it.
   - [ ] Propose scope: start strict on `latextify/privacy/` and `latextify/model/`, widen from there
   - [ ] Add mypy config to `pyproject.toml` for chosen scope (strict mode)
   - [ ] Add type-check step to CI workflows before tests
   - [ ] Fix any violations in chosen scope (expect moderate fallout; annotations exist, logic is sound)

2. **Version-agreement guard** — `pyproject.toml` and `uv.lock` both record the version; nothing asserts they agree.
   - [ ] Add the assertion to `tests/test_repo_integrity.py` (the existing home for repo-wide guards)
   - [ ] Failure message must name the disagreeing file and both values
   - [ ] Note the Python 3.10 floor: `tomllib` is 3.11+, so match whatever TOML-reading precedent the repo already uses, or use a narrow regex with a comment explaining why
   - [ ] Update documented release checklist against a real release commit history to catch missing files

## Tier 2 — Medium Impact

3. **Decide the `ruff format` question** — formatter is declared but unenforced; 80 of ~179 files want reformatting, CI runs only `check`, not `format`.
   - [ ] Decide: adopt repo-wide (one mechanical commit, add to CI) or stop implying it applies
   - [ ] If adopting: run `ruff format` once, add to CI config, document in CONTRIBUTING
   - [ ] If dropping: remove `format` section from `pyproject.toml` and any docs mentioning it

4. **Decompose `latextify/gui/server.py`** — 916 lines against 921-line pin, effectively frozen.
   - [ ] Survey the file's sections (auth, uploads, conversions, health, static, config)
   - [ ] Plan extraction targets: likely `gui/auth_routes.py`, `gui/conversion_routes.py`, etc.
   - [ ] Extract largest cohesive sections to new modules to reclaim headroom
   - [ ] Update pin downward as extraction shrinks the file
   - [ ] Verify: next GUI feature can now land without extraction

6. **Split `latextify/figures/convert.py`** — 495 lines against the general
   500-line ceiling. The next file to hit the wall, and unlike `server.py` it
   has no pin to hide behind: crossing 500 fails the ratchet outright.
   - [ ] Move `ConversionOutcome` to its own module — every per-format
         converter is currently bound to `convert.py` by that one dataclass,
         which is what makes them unextractable
   - [ ] Split the SVG, EPS and TIFF converters out, leaving
         `convert_for_latex`/`_dispatch` as the dispatcher they already are
   - [ ] Verify: the next figure-format feature lands without extraction

## Tier 3 — Nice-to-Have

5. **Add a `.python-version`** — CI workflows hardcode Python versions; no repo file to derive them from.
   - [ ] Create `.python-version` in repo root with the pinned version (e.g. `3.13`)
   - [ ] Update workflows to use `actions/setup-python` with `python-version-file: .python-version`
   - [ ] Verify workflows read the file, not hardcoded fallbacks

---

## Owner gates

- **Type-checking scope** — start narrow (two packages) and widen, or attempt full codebase at once? Blocks item 1.
- **`ruff format` adoption** — adopt repo-wide (large mechanical diff, `git blame` churn) or drop it? Blocks item 3.

---

## Completed
