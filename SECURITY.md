# Security Policy

## Supported versions

Only the latest commit on `main` is supported. There are no maintained
release branches yet.

## Reporting a vulnerability

Please use **GitHub private vulnerability reporting**: open the repository's
*Security* tab and click *Report a vulnerability*. Do not open a public
issue for security problems.

You can expect an acknowledgement within a week. There is no bug bounty.

## Threat model

LaTeXtify is a **local desktop tool for a single user**. The supported
deployment is: you run the CLI or `latextify gui` on your own machine, on
manuscripts you chose to convert. The GUI binds to `127.0.0.1` only.
Hosting the GUI on a LAN or public server is unsupported; reports assuming
that deployment are still welcome but out of the supported scope.

The one adversarial input we defend against in depth is the **manuscript
itself**: a `.docx` (or `.odt`/`.rtf`/`.md`) file may come from an untrusted
collaborator or an email attachment and is treated as hostile.

### Untrusted manuscript handling limits

`.docx` files are ZIP archives; every archive is validated at the ingest
boundary (`latextify/ingest/archive_guard.py`) before any XML parsing,
rewriting, or Pandoc invocation:

- at most 2,048 members; 256 MiB expanded per member; 1 GiB expanded total;
- compression ratio capped at 200:1 (bomb rejection), measured only for
  members over 4 KiB compressed;
- encrypted members rejected; absolute, parent-traversing, NUL-containing,
  and case-folded-duplicate member names rejected;
- member copies are streamed in 1 MiB chunks with byte counting, so a
  header that lies about its expanded size is caught mid-copy.

The GUI additionally caps each upload at 250 MB per file, and XML is parsed
with hardened settings (no external entity resolution).

### Local GUI request protection

Binding to loopback does not stop a malicious web page you happen to be
visiting from scripting background requests at `http://127.0.0.1` (localhost
CSRF, DNS rebinding). Mutating `/api/*` endpoints therefore require all of
(`latextify/gui/guard.py`):

- a random per-process secret, minted at startup and known only to the
  served page, attached as a request header and compared in constant time;
- a loopback-literal `Host` header (defeats DNS rebinding);
- a loopback `Origin` when one is present.

Compiled PDFs and export ZIPs are streamed only via opaque server-issued
`uuid4` tokens — an unknown or tampered token is a 404, never a filesystem
path lookup.

### Executable bootstrap verification

Compiling LaTeX runs **Tectonic** on generated input. When LaTeXtify
bootstraps the Tectonic binary itself (`latextify/compile/tectonic.py`), it
downloads a **pinned release version** whose per-platform asset filename and
SHA-256 are recorded in the source; the download is size-capped, streamed,
and discarded on any checksum mismatch (fail closed — a missing target or
bad digest never installs anything). Archive extraction accepts only a
single root-level `tectonic` binary and rejects symlinks and traversal
paths. Tectonic's own sandboxing behavior is upstream's domain; issues in
how LaTeXtify invokes it are in scope.

### Temporary-data lifetime

GUI conversions run in per-session directories under a server-owned
temporary root:

- a previewed conversion stays exportable for **1 hour** after its last
  active use (TTL refreshed by preview/export/correction activity), then
  its directory is deleted;
- session count is additionally LRU-capped, and download tokens whose
  backing files are gone are pruned;
- the server's own temporary root is swept on shutdown.

Nothing is uploaded anywhere: all conversion, compilation, and optional
reference matching happen locally, except the explicitly opt-in online
checks (e.g. Crossref DOI lookup), which send only citation metadata —
never the manuscript.

## Scope notes

- Parser robustness issues (crashes, path traversal via archive members,
  XML issues) in untrusted-manuscript handling are in scope and taken
  seriously.
- OS/container-level sandboxing of Pandoc/TeX is currently out of scope
  (tracked as a possible future hardening pass).
