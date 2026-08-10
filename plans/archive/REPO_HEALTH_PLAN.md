# LaTeXtify — Repo Health & Release Safety

Cross-cutting maintainability and release-safety gaps found during codebase audit (2026-08-10): type-checking deaf spot, version-agreement drift risk, formatter declaration-but-unenforced, module size ceiling approaching, and toolchain version fragility.

**Status:** Complete
**Created:** 2026-08-10
**Updated:** 2026-08-10 (items 1-6 all shipped; plan complete)

---

## Context

### How the pieces fit together

_Everything in this section describes the repo as it was BEFORE the work
below landed, and is kept as the record of what the plan was responding to.
The numbers are all stale now by design — see `## Completed`._

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

## Owner gates

Both resolved by the owner on 2026-08-10:

- **Type-checking scope** — start narrow (`privacy/` + `model/`, strict) and
  widen from there, rather than attempting the full codebase at once.
- **`ruff format` adoption** — adopt repo-wide, accepting the one-time
  `git blame` churn, rather than dropping the formatter.

---

## Completed

- ~~**#1 Adopt type checking**~~ (2026-08-10) — strict mypy on
  `latextify/privacy` + `latextify/model` (22 files), a CI step before the
  test job, and `mypy>=1.11` in dev deps. Only 13 errors in the chosen scope
  and `model/` was already clean. Notably NOT solved with
  `ignore_missing_imports`: `lxml-stubs` and `types-olefile` are dev
  dependencies so lxml and olefile are genuinely checked. Two real findings —
  a `# type: ignore[union-attr]` in `privacy/images.py` naming an error code
  that never fired, so it silenced nothing while hiding that a GPS coordinate
  was iterated as bare `object` (now shape-checked), and `im.n_frames` read
  directly under a `getattr` guard. Pinning `python_version = "3.10"` (the
  documented floor) is what surfaced the `StrEnum` backport mismatch, which is
  a genuine mypy limitation and carries a targeted ignore explaining why.

- ~~**#2 Version-agreement guard**~~ (2026-08-10) —
  `test_declared_versions_agree` asserts `pyproject.toml` and `uv.lock` record
  the same version, naming both values on failure. A narrow anchored regex,
  not `tomllib` (3.11+, and the floor is 3.10). No `RELEASE.md` was written:
  the guard is what makes a checklist unnecessary rather than merely absent.

- ~~**#3 `ruff format` adoption**~~ (2026-08-10) — adopted repo-wide (82 of
  189 files reformatted), `ruff format --check .` added to CI, documented in
  CONTRIBUTING. It re-measured the tree honestly: `kit/build.py` went 468 →
  554, past the ceiling, and had to be split (`kit/target.py`,
  `kit/tex_cache.py`); `figures/convert.py` went 495 → 500.

- ~~**#4 Decompose `gui/server.py`**~~ (2026-08-10) — 909 → 437, **pin
  deleted**. The two conversion routes (467 lines) became
  `gui/convert_routes.py`; the ratchet then rejected THAT at 529, which
  produced `gui/convert_inputs.py` (form validation + upload staging, now
  unit-testable without a request). The `_MAX_SESSIONS`/`_prune_sessions`/
  `_export_artifacts` re-export shims went too — one import path per name.

- ~~**#5 Add a `.python-version`**~~ (2026-08-10) — created, and the jobs that
  want "the default dev interpreter" now derive from it (setup-uv reads it
  natively) instead of hardcoding 3.12 in three workflows. The test matrix and
  the oldest/newest offline-kit jobs keep explicit pins, because exercising a
  RANGE is their whole point. `test_python_version_file_matches_ci` asserts
  the file is not below the `requires-python` floor.

- ~~**#6 Split `figures/convert.py`**~~ (2026-08-10) — 500 → 182. The blocker
  was `ConversionOutcome`: every per-format converter returns one, so none
  could leave while the dataclass lived there. `figures/outcome.py` unlocked
  `figures/vector.py` and `figures/raster.py`. `cli.py` (484 → 164) and
  `ingest/filters.py` (1059 → 402, **pin deleted**) were split in the same
  pass; filters.py yielded `ingest/headings.py`, `ingest/tables.py` and
  `ingest/tables_degraded.py`.
