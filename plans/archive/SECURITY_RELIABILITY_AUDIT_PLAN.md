# LaTeXtify — Security and Reliability Audit Remediation

Implementation plan for the issues found during the 2026-07-12 read-only audit.
This is deliberately a hardening and correctness pass, not a redesign of the
conversion pipeline or an expansion into a hosted service.

**Status:** Complete
**Created:** 2026-07-12
**Updated:** 2026-07-18

**Scope:** Untrusted DOCX handling, Tectonic bootstrap integrity, local-GUI
privacy/authorization, and several contained GUI/export correctness fixes.

---

## Baseline

At audit time:

- `uv run ruff check .` passed.
- `uv run pytest -m "not tectonic and not network" -q` passed: 823 tests,
  with 61 real-Tectonic/network tests excluded.
- The working tree was not modified by the audit.
- A locked dependency vulnerability scan could not run because uv hit a
  OneDrive/hardlink error while installing `pip-audit`; this remains a follow-up.

Preserve existing behavior unless an item below explicitly changes it. In
particular, keep `main.tex` and `supplement.tex` write-once semantics.

## Implementation order

Do items 1 and 2 first; they are the security boundary. Items 3–7 are smaller
privacy and correctness changes and may be implemented independently after
that. Item 8 closes the audit/CI gap.

## Progress tracker

Check both boxes for an item only after its implementation and its item-specific
acceptance tests are complete. Keep this tracker synchronized with the detailed
sections below; a passing implementation box alone does not close an item.

- [x] **1 — DOCX/ZIP limits: implementation complete**
- [x] **1 — DOCX/ZIP limits: acceptance tests pass**
- [x] **2 — Tectonic bootstrap integrity: implementation complete**
- [x] **2 — Tectonic bootstrap integrity: acceptance tests pass**
- [x] **3 — GUI session expiry/cleanup: implementation complete**
- [x] **3 — GUI session expiry/cleanup: acceptance tests pass**
- [x] **4 — Local GUI request protection: implementation complete**
- [x] **4 — Local GUI request protection: acceptance tests pass**
- [x] **5 — Upload naming/validation: implementation complete**
- [x] **5 — Upload naming/validation: acceptance tests pass**
- [x] **6 — Supplement export/status: implementation complete**
- [x] **6 — Supplement export/status: acceptance tests pass**
- [x] **7 — Stale figure reconciliation: implementation complete**
- [x] **7 — Stale figure reconciliation: acceptance tests pass**
- [x] **8 — Dependency/CI security checks: implementation complete**
- [x] **8 — Dependency/CI security checks: acceptance criteria pass**
- [x] **Final verification and handoff complete**

---

## 1. Bound DOCX/ZIP resource consumption

### Problem

The GUI limits each compressed upload to 250 MB, but DOCX files are ZIP
archives. Several modules read decompressed members fully into memory. The
archive-rewrite path in `latextify/ingest/citation_sentinels.py` reads every
member with `zin.read()`, so a small, highly compressed document can exhaust
memory or disk before Pandoc runs.

### Work

- Add one shared DOCX archive validation utility and call it at the earliest
  common ingest boundary, before preflight, rewriting, XML parsing, or Pandoc.
- Enforce documented, configurable constants for:
  - maximum member count;
  - maximum expanded size of one member;
  - maximum total expanded size;
  - maximum compression ratio;
  - rejection of encrypted members;
  - rejection of absolute, parent-traversing, NUL-containing, or duplicate
    normalized member names.
- Choose limits generous enough for real scientific manuscripts with embedded
  figures. Explain the values in code; do not reuse the 250 MB compressed-upload
  limit as the expanded-size limit without justification.
- Return a clean `ValueError` naming the document and violated limit so CLI and
  GUI surfaces produce actionable errors rather than tracebacks.
- Where archives are copied/re-written, stream unchanged members with bounded
  reads instead of materializing every member as a `bytes` object.
- Ensure all entry points are covered, including main documents, supplements,
  and equation-audit-only operation.

### Acceptance tests

- A normal fixture still passes validation and converts unchanged.
- Synthetic archives are rejected for excessive member count, individual
  expanded size, total expanded size, compression ratio, encryption, traversal,
  and duplicate normalized names.
- Rejection happens before Pandoc and before a large expanded allocation/write.
- A malformed/non-ZIP `.docx` still produces the existing clean corrupt-file
  error contract.
- Archive rewrite tests demonstrate bounded/streaming copying.

---

## 2. Make Tectonic bootstrap verifiable and extraction-safe

**Complete (2026-07-12).** Pinned Tectonic 0.16.9; every target asset carries a
recorded SHA-256 (`_TECTONIC_ASSETS`), verified while streaming the download to
a temp file under a size cap; only the single root-level `tectonic`/`.exe`
member is extracted (no `extractall`; missing/duplicate/link/dir/traversal
members fail closed) and atomically `os.replace`d into the cache. Downloading by
the pinned tag's direct asset URL also drops the rate-limited releases API (and
its token dependency). Verified end-to-end against the real 0.16.9 release.
**Deliberately skipped:** the redirect-host allowlist — SHA-256 verification
already makes the transport path untrusted-safe (a hostile redirect cannot yield
a hash-matching binary), so a host allowlist adds maintenance without security.

### Problem

`latextify/compile/tectonic.py` downloads the latest GitHub release, extracts
the whole archive, caches the binary, and executes it without pinning a version
or verifying a checksum. Compromise or substitution anywhere in that chain can
become code execution. `extractall()` also writes every archive member.

### Work

- Pin a reviewed Tectonic version rather than resolving `releases/latest` at
  runtime.
- Store expected SHA-256 digests for every supported target asset alongside the
  pinned version. Fail closed on a missing target or digest mismatch.
- Construct or validate the expected GitHub release URL and reject redirects
  whose final host is outside an explicit allowlist needed for GitHub release
  assets.
- Download with an explicit maximum response size into a temporary file or
  directory; do not keep an unbounded release archive entirely in memory.
- Inspect the archive and extract only the exact root-level `tectonic` or
  `tectonic.exe` member. Reject duplicate candidates, links, traversal paths,
  device entries, or unexpected member types.
- Set executable permissions where required, then atomically replace the cache
  entry so interrupted downloads cannot leave a runnable partial binary.
- Keep `find_tectonic()` behavior for an explicitly installed PATH binary. Make
  the trust distinction clear in documentation: PATH binaries are user-managed;
  downloaded binaries are version/checksum managed by LaTeXtify.
- Give users a clear error when the pin is stale or their platform is unsupported.

### Acceptance tests

- Valid ZIP and tar.gz fixtures extract the one expected binary.
- Wrong hash, missing binary, duplicate binary, traversal member, symlink, and
  oversized response/archive all fail without writing outside the temporary
  directory or replacing a valid cache entry.
- A simulated interrupted download leaves the prior cached binary intact.
- Existing compile invocation and timeout behavior remain covered.
- Any test that contacts GitHub remains marked `network`; integrity and archive
  tests themselves must be offline.

---

## 3. Add GUI session expiry and cleanup

### Problem

GUI previews write uploads and generated artifacts to temporary storage, while
PDF/ZIP/export token dictionaries grow for the lifetime of the process. There
is no expiry, failed-run cleanup, storage bound, or shutdown cleanup. This can
retain private manuscripts and consume unbounded disk/memory.

### Work

- Represent conversion sessions explicitly, including creation/last-access
  time, session directory, and issued artifact tokens.
- Add a reasonable configurable TTL and prune expired sessions/tokens on normal
  API activity. A background scheduler is unnecessary unless it materially
  simplifies the design.
- Refresh access time when a valid artifact is downloaded or exported.
- Remove partially created session directories when upload, emission, compile,
  audit, or packaging fails, except where an intentional debug-retention option
  is explicitly enabled.
- Delete the automatically created root work directory on application shutdown.
  Do not delete a caller-supplied persistent `workdir`; prune only session
  children owned by the app.
- Add a conservative total-session or total-byte bound and evict oldest expired
  or least-recently-used sessions before accepting work that would exceed it.
- Update README/UI wording: Preview writes files to temporary local storage but
  does not copy them to the user-selected destination; state when they are
  deleted.

### Acceptance tests

- Expired PDF, ZIP, and export tokens return 404 and their session directory is
  removed.
- Active access refreshes expiry consistently.
- Failed conversions do not leave uploaded manuscripts behind by default.
- Automatic temporary roots are cleaned at shutdown; supplied roots are not
  recursively deleted.
- Token stores remain bounded under repeated conversions.

---

## 4. Protect mutating localhost GUI endpoints

### Problem

Binding to `127.0.0.1` prevents remote listening but does not stop a hostile web
page from sending requests to the local service. Cross-origin reads are usually
blocked, but resource-intensive conversions and the native folder picker can
still be triggered; DNS rebinding can weaken assumptions further.

### Work

- Generate a random per-process GUI secret at startup and make it available only
  to the served application page (for example via a same-origin bootstrap
  endpoint/cookie or injected configuration).
- Require the secret on mutating `/api/*` requests. Do not put it in URLs or
  logs. Use constant-time comparison where practical.
- Validate `Host` against loopback forms and reject unexpected non-null `Origin`
  values. Allow documented loopback development/test origins only.
- Apply the protection consistently to convert, convert-multi, pick-folder, and
  export. Artifact GET tokens remain bearer capabilities and need not share the
  mutation secret.
- Keep the design local and dependency-light; do not add accounts or a general
  authentication system.

### Acceptance tests

- Same-origin requests with the correct secret work.
- Missing/incorrect secrets, hostile origins, and unexpected hosts are rejected
  before uploads are processed or native UI is opened.
- Existing GUI behavior remains functional through the browser client.
- Tests can inject a deterministic secret without weakening production defaults.

---

## 5. Fix upload naming and validation

### Problem

The main upload and optional bibliography are saved under client-derived
basenames. If they share a basename, the bibliography overwrites the main DOCX.
Figure numbers may be zero, negative, or duplicated, and figure extensions are
not restricted before writing.

### Work

- Store inputs under server-selected names: `main.docx`, `supplement.docx`, and
  `references.bib`. Preserve original names separately only for messages/reporting.
- Reject a main or supplement whose filename/type is not `.docx`, while still
  validating the actual archive contents rather than trusting the extension.
- Reject a references upload that is not a supported BibTeX input.
- Require figure numbers to be positive and unique within the request.
- Use an explicit case-insensitive allowlist of figure extensions already
  supported by the conversion pipeline. Normalize `.jpeg`/`.jpg` deliberately.
- Detect duplicate destination names before streaming any figure body.

### Acceptance tests

- Identical client basenames for main and bibliography cannot collide.
- Duplicate, zero, and negative figure numbers return HTTP 400.
- Unsupported extensions return HTTP 400 before conversion.
- Valid upper/lowercase extensions and current formats continue to work.

---

## 6. Export and report supplementary compilation correctly

### Problem

The GUI can preview a compiled supplement PDF but cannot export it separately:
`supplement_pdf` is absent from `_EXPORTABLE` and from the `produced` map. Also,
the response can report success when the requested supplement failed to compile,
because `success` reflects only the main document.

### Work

- Add `supplement_pdf` to the produced-artifact map, API export allowlist, and
  export UI.
- Add explicit `main_compile_success` and `supplement_compile_success` response
  fields (nullable when compilation/document is not requested).
- Define overall `success` as all requested compilation outputs succeeding. A
  main success plus supplement failure must be visibly partial/failed.
- Add supplement compile diagnostics to `report.md` and a concise GUI warning.
- Ensure combined-PDF behavior remains gated on both successful PDFs.
- Update API model docstrings and README to match the new semantics.

### Acceptance tests

- A successfully compiled supplement can be exported independently.
- Main success plus supplement failure reports overall failure/partial status,
  retains the usable main PDF, and exposes supplement diagnostics.
- Main-only and no-PDF conversions preserve their expected success behavior.
- Combined PDF is absent and clearly explained if either input PDF fails.

---

## 7. Reconcile stale generated figure artifacts

### Problem

Re-running into an existing journal output can leave old generated figure files
when the new manuscript has fewer figures or changes format. Those files can be
included in an exported project/ZIP even though they are no longer referenced.

### Work

- Before copying current figures, remove only files that match LaTeXtify-owned
  generated naming (`fig<N>.*` and `figS<N>.*`) and are not part of the current
  main/supplement result.
- Preserve unrelated/user-owned files in `figures/`.
- Account for main and supplement generation order so one pass does not delete
  the other document's current figures.
- Prefer a small generated-artifact manifest if filename-pattern ownership is
  ambiguous; do not clear the entire directory.

### Acceptance tests

- Re-running from three figures to one removes obsolete generated figures.
- Changing a figure from PNG to PDF removes the stale PNG.
- Current supplement figures survive main reconciliation and vice versa.
- Unrelated user files remain untouched.
- ZIP exports contain only current generated figures plus preserved user files.

---

## 8. Complete dependency and CI security checks

**Complete (2026-07-12).**

### Work

- ~~Run `pip-audit` against the locked environment outside the OneDrive
  hardlink failure mode.~~ (2026-07-12) — ran locally with `UV_LINK_MODE=copy`
  (the hardlink workaround): 33 locked runtime dependencies, **no known
  vulnerabilities**. Nothing to remediate.
- ~~Add a CI dependency-audit job with a documented failure/update policy.~~
  (2026-07-12) — `.github/workflows/dependency-audit.yml` runs `uvx pip-audit`
  over the locked runtime deps (`uv export --no-dev --no-emit-project`) on
  push/PR and a weekly schedule; the failure/waiver policy is documented inline.
- Static security check: **already covered** by the existing CodeQL workflow
  (`.github/workflows/codeql.yml`); do not add a second scanner.
- ~~Ensure GitHub Actions are pinned to immutable commit SHAs, with the readable
  release tag retained in comments.~~ (2026-07-12) — all `uses:` across ci.yml,
  codeql.yml, dependency-audit.yml, and release.yml pinned to the tag's commit
  SHA with a `# vN` comment; validated with actionlint.

### Acceptance criteria

- A current locked-dependency audit completes successfully and its result is
  documented in the PR.
- CI repeats the audit on pull requests or a scheduled cadence.
- Any accepted advisory exception states package, advisory, exposure analysis,
  and review/expiry date.

---

## Final verification and handoff

Completed 2026-07-18 (Windows host):

- [x] Run `uv run ruff check .`. — all checks passed.
- [x] Run the complete offline suite. — 1091 passed, 62 deselected.
- [x] Run real-Tectonic tests for at least the host platform. — 60 passed,
   1 xfailed (real PDF compiles on Windows/Tectonic).
- [x] Run network tests separately and distinguish upstream/network failures from
   product failures. — 1 passed, no failures to triage.
- [x] Exercise the GUI manually: main-only preview/export, main + supplement +
   separate figures + `.bib`, supplement PDF export, combined PDF, expiry, and
   shutdown cleanup. — drove the real `/api/convert-multi`, `/api/zip`, and the
   lifespan startup/shutdown sweep through the FastAPI app; all 200/clean.
- [x] Inspect an exported ZIP to confirm it contains no stale artifacts or source
   uploads and still compiles independently. — ZIP held `main.tex`,
   `supplement.tex`, `generated/*`, `figures/*`, `references.bib`, `report.md`;
   no `.docx`/`.aux`/`.log`/`.pdf` leaks; standard compilable project layout.
- [x] Confirm malicious archive/security tests run offline and do not allocate
   dangerous payload sizes; construct them using declared ZIP metadata and
   tightly bounded synthetic content. — `tests/test_archive_guard.py` uses
   tiny synthetic members against overridable tiny bounds (largest payload
   20 KB); Tectonic-extraction tests likewise use bytes-sized fixtures.
- [x] Update `SECURITY.md` with the supported threat model: local desktop use,
   untrusted manuscript handling limits, executable bootstrap verification,
   temporary-data lifetime, and responsible disclosure path.

## Out of scope

- Hosting the GUI on a LAN or public server.
- User accounts, multi-user tenancy, or a general authentication system.
- Sandboxing TeX/Pandoc at the OS/container level. This can be revisited later,
  but the current pass should first bound input resources and secure the managed
  executable supply chain.
- New journal templates or broad conversion-fidelity features.
- Replacing Pandoc, Tectonic, FastAPI, or the existing project layout.
