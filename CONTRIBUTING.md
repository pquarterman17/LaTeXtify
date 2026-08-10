# Contributing

Thanks for your interest! This is a young project; issues and PRs are welcome.

## Development setup

```
git clone https://github.com/pquarterman17/LaTeXtify
cd LaTeXtify
uv sync          # creates .venv, installs everything incl. dev deps
uv run pytest    # full suite (downloads the Tectonic binary on first run)
uv run ruff check .
uv run ruff format .   # formatting is enforced in CI, not just available
```

Fast subset (no TeX engine, no network):

```
uv run pytest -m "not tectonic and not network"
```

The `tectonic`-marked tests skip unless the Tectonic binary is already on
PATH or in the cache — the skip guards deliberately never download at
collection time. Fetch it once and they activate:

```
uv run python -c "from latextify.compile.tectonic import ensure_tectonic; print(ensure_tectonic())"
```

## Ground rules

- **Tests first-class:** every bug fix carries a minimal reproducing test;
  every feature carries tests. The suite must be green (`pytest` + `ruff
  check .` + `ruff format --check .`) before a PR.
- **Modules stay small:** `tests/test_repo_integrity.py` enforces a 500-line
  ceiling on every source file, with a handful of legacy files pinned at
  their current size. Pins only ever move DOWN — if a change would push a
  file over, extract to a focused module rather than raising the number.
- **Journals are data:** adding a journal means adding a folder under
  `latextify/templates/journals/` (manifest.yaml + two Jinja templates +
  golden-file tests) — never editing converter code. Copy `revtex4-2/` as
  the worked example, and check vendored files' licenses before committing
  them (see `sn-jnl/` and `iopart/` for the pattern; Wiley shows the
  documented-skip pattern for non-redistributable classes).
- **IR discipline:** data crossing stage boundaries uses the frozen
  dataclasses in `latextify/model/` — no ad-hoc dicts.
- **Commit style:** `type(scope): imperative description` (feat, fix,
  refactor, docs, test, chore).
- `.docx` test fixtures are generated, never hand-edited: each has a
  committed `tests/fixtures/make_<name>.py` script.

## Architecture orientation

Read `plans/archive/LATEXTIFY_PLAN.md`'s Context section — it documents the
pipeline, the write-once/`generated/` output contract, and the design
decisions with dates.
