# Deploying the LaTeXtify demo to a Hugging Face Space

> **Cost note (2026-07):** Hugging Face now requires a **PRO subscription
> ($9/month)** to host Docker Spaces, even on the free cpu-basic hardware
> (repo creation fails with HTTP 402 otherwise). The zero-cost deployment
> for this demo is Render's free tier -- see `deploy/render/DEPLOY.md` and
> the repo-root `render.yaml`. This kit remains the better-hardware option
> (2 vCPU / 16 GB) if the subscription is ever worth it.

This directory holds the exact contents of the Space repo: `Dockerfile`,
`README.md` (the Space card — its YAML frontmatter configures the Space), and
`warm_cache.py`. This `DEPLOY.md` stays in the main repo only.

## One-time setup

1. Create the Space at <https://huggingface.co/new-space>:
   - **Owner/name:** e.g. `<your-hf-user>/latextify`
   - **License:** Apache-2.0, **SDK:** *Docker* (blank template),
     **Hardware:** CPU basic (free), **Visibility:** public
2. Clone it and copy the three files in:

   ```bash
   git clone https://huggingface.co/spaces/<your-hf-user>/latextify hf-latextify
   cd hf-latextify
   cp <main-repo>/deploy/hf-space/{Dockerfile,README.md,warm_cache.py} .
   git add -A && git commit -m "deploy: LaTeXtify demo" && git push
   ```

   Pushing triggers the image build. The first build takes several minutes:
   it installs LaTeXtify from GitHub, downloads the Tectonic binary, and
   warms the LaTeX package cache (`warm_cache.py`).
3. When the build finishes the demo is live at
   `https://<your-hf-user>-latextify.hf.space` (and embedded on the Space page).
4. Add the URL to the main repo's README "hosted demo" link.

## Updating to a new LaTeXtify version

The Dockerfile installs LaTeXtify from the GitHub ref in `ARG LATEXTIFY_REF`
(default `main`). Docker caches that layer by the *ref string*, not by what
the branch points at — so to deploy new code, pin an exact commit or tag:

```dockerfile
ARG LATEXTIFY_REF=v0.2.0        # or a full commit SHA
```

Commit and push to the Space; only the install layer and later rebuild.
Alternatively use *Settings → Factory rebuild* on the Space to force a full
no-cache rebuild without changing the ref.

## What the demo hardening does (latextify/gui/demo.py)

- `python -m latextify.gui.demo` runs `create_app(demo=True)` on
  `LATEXTIFY_DEMO_HOST:LATEXTIFY_DEMO_PORT` (the Dockerfile sets
  `0.0.0.0:7860`; `app_port: 7860` in the README frontmatter must match).
- Server-filesystem endpoints (`/api/pick-folder`, `/api/export`, inline
  `export_dir`) return 403; visitors download the PDF / project `.zip`.
- 25 MB per-file upload cap (local default is 250 MB).
- 10 conversions per hour per client IP (first `X-Forwarded-For` hop), HTTP
  429 with `Retry-After` beyond that.
- A privacy banner is injected into the served page; the Export panel is
  hidden. The CSRF secret guard stays active with a same-origin check in
  place of the local loopback-only checks.
- Uploads and generated artifacts live under a temp workdir with the same
  1-hour TTL / LRU session pruning as the local tool, and the whole tree is
  deleted on shutdown.

## Operational notes

- **Free CPU Spaces sleep** after ~48 h without traffic; the next visitor
  wakes the Space (cold start ≈ container boot, no rebuild).
- Conversion handlers do their heavy work on the event loop, so requests
  naturally serialize — one compile at a time. Fine for a demo; do not point
  real traffic at it.
- Crossref-backed reference checking needs outbound network, which Spaces
  allow. The Tectonic package cache is pre-warmed for `article` and
  `revtex4-2`; other classes fetch missing packages on first use.
- Nothing here needs an HF token or secret; the Space runs unauthenticated.
