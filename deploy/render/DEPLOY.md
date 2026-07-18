# Deploying the LaTeXtify demo to Render (free tier)

The repo root's `render.yaml` Blueprint + `deploy/render/Dockerfile` deploy
the hardened demo server (`latextify/gui/demo.py`) as a Render web service.
Chosen over Hugging Face Spaces because HF now requires a PRO subscription
($9/month) for Docker Spaces; Render's free tier costs nothing and needs no
credit card.

## One-time setup (owner)

1. Sign in at <https://dashboard.render.com> ("Sign in with GitHub" is
   easiest) and authorize Render to see the `pquarterman17/LaTeXtify` repo.
2. **New → Blueprint**, pick the LaTeXtify repo, branch `main`. Render reads
   `render.yaml` and shows the `latextify-demo` service on the **free** plan.
   Click *Apply*.
3. First build takes a while: it installs the package and bakes/warms the
   Tectonic caches (`warm_cache.py`). When it turns *Live*, the demo is at
   `https://latextify-demo.onrender.com` (exact URL shown on the service page).
4. Add that URL to the main README's demo link.

## Updating

`autoDeploy` is off, so pushes to main do NOT redeploy. To ship a new
version: service page → **Manual Deploy → Deploy latest commit**. Flip
`autoDeploy: true` in `render.yaml` if tracking main automatically is ever
preferred.

## Free-tier expectations (why the demo feels slow)

- **0.1 vCPU / 512 MB RAM.** A LaTeX compile that takes ~20 s locally can
  take minutes here, and very figure-heavy manuscripts may exhaust memory
  and fail. This is a proof-of-concept tier.
- **Sleeps after ~15 min idle**; the next visitor waits ~30-60 s for wake-up.
- **Proxy timeout risk:** Render's edge closes very long HTTP requests
  (~100 s). Small/text-only papers should convert within it thanks to the
  pre-warmed Tectonic cache; a conversion that exceeds it fails at the HTTP
  layer even though the server finishes. If this bites often, the options
  are: upgrade the instance (Starter, $7/mo), default the demo's "Compile
  PDF" toggle off (LaTeX project .zip only -- still a full demo of the
  conversion), or move to a job-queue + polling API (real work).
- Same knobs as any demo deployment: 25 MB upload cap, 10 conversions/hour
  per IP, filesystem export disabled (see `latextify/gui/demo.py`).

## Relationship to deploy/hf-space/

That kit targets a Hugging Face **Docker Space** and still works -- it just
requires HF PRO now. Both deployments run the same `python -m
latextify.gui.demo` entry point; only the packaging differs (HF installs a
pinned GitHub ref into a standalone Space repo, Render builds this repo
directly). `warm_cache.py` is shared from `deploy/hf-space/`.
