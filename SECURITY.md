# Security Policy

## Supported versions

Only the latest commit on `main` is supported. There are no maintained
release branches yet.

## Reporting a vulnerability

Please use **GitHub private vulnerability reporting**: open the repository's
*Security* tab and click *Report a vulnerability*. Do not open a public
issue for security problems.

You can expect an acknowledgement within a week. There is no bug bounty.

## Scope notes

- LaTeXtify parses untrusted `.docx` files (ZIP + XML). Parser robustness
  issues (crashes, path traversal via archive members, XML issues) are in
  scope and taken seriously.
- The GUI (`latextify gui`) binds to 127.0.0.1 only and is intended for
  local, single-user use. Reports assuming it is exposed to a network are
  still welcome, but that deployment is unsupported.
- Compiling LaTeX runs Tectonic on generated input. Tectonic's own sandbox
  behavior is upstream's domain; issues in how LaTeXtify invokes it are in
  scope.
