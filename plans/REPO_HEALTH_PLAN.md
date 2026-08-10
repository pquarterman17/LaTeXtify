# LaTeXtify — Repo Health & Release Safety

Cross-cutting maintainability and release-safety gaps found during codebase audit (2026-08-10): type-checking deaf spot, version-agreement drift risk, formatter declaration-but-unenforced, module size ceiling approaching, and toolchain version fragility.

**Status:** Active
**Created:** 2026-08-10
**Updated:** 2026-08-10

---

## Context

### How the pieces fit together

LaTeXtify is a mature single-repo project with ~20k lines of Python, comprehensive type annotations in-place but unverified, and multiple files declaring versions with no guard. A release checklist documents what to bump; a previous Anthropic project (quantized, 2026-07-30) surfaced the hard way that the checklist can drift from reality (undocumented `uv.lock` stale = `--locked` build fails). `latextify/gui/server.py` is 916 lines against a 921-line pin, effectively frozen; any GUI feature must extract first. CI workflows hardcode Python versions with no `.python-version` to derive from.

### Dependency map

- **Type checking (item 1)** is independent; blocks nothing yet, depends on owner decision (strategy: narrow start or full coverage).
- **Version agreement (item 2)** is independent.
- **Ruff format (item 3)** is independent; depends on owner decision (adopt or drop).
- **Server.py decomposition (item 4)** is needed whenever the next GUI feature lands; planned deliberately rather than under deadline.
- **Python version file (item 5)** is independent, nice-to-have.

---

## Tier 1 — High Impact

1. **Adopt type checking** — the codebase is thoroughly annotated but NOTHING verifies any of it.
   - [ ] Verify no `mypy` config exists in `pyproject.toml`
   - [ ] Verify no type-check step in GitHub workflows
   - [ ] Propose scope: start strict on `latextify/privacy/` and `latextify/model/`, widen from there
   - [ ] Add mypy config to `pyproject.toml` for chosen scope (strict mode)
   - [ ] Add type-check step to CI workflows before tests
   - [ ] Fix any violations in chosen scope (expect moderate fallout; annotations exist, logic is sound)

2. **Version-agreement guard** — project version declared in multiple files, no test asserts they agree.
   - [ ] Find every file declaring version (expect `pyproject.toml`, `uv.lock`)
   - [ ] Add assertion to `tests/test_repo_integrity.py` naming files and both values
   - [ ] Fix any mismatches found
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
