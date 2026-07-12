# Contributing

Thanks for your interest! This is a young project; issues and PRs are welcome.

## Development setup

```
git clone https://github.com/pquarterman17/LaTeXtify
cd LaTeXtify
uv sync          # creates .venv, installs everything incl. dev deps
uv run pytest    # full suite (downloads the Tectonic binary on first run)
uv run ruff check .
```

Fast subset (no TeX engine, no network):

```
uv run pytest -m "not tectonic and not network"
```

## Ground rules

- **Tests first-class:** every bug fix carries a minimal reproducing test;
  every feature carries tests. The suite must be green (`pytest` + `ruff
  check .`) before a PR.
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
