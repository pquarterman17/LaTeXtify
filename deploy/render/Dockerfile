# Render.com image for the LaTeXtify public demo (free-tier web service).
#
# Unlike deploy/hf-space/Dockerfile (which pip-installs a pinned GitHub ref
# into a standalone Space repo), Render builds straight from this repository:
# the build context is the repo root (see render.yaml: dockerContext "."), so
# the checked-out source is installed directly.
FROM python:3.12-slim

# libcairo2 lets cairosvg (the preferred SVG->PDF converter) load its native
# backend; latextify falls back to pure-Python svglib without it.
# libfontconfig1 + libssl3 are hard requirements of the Tectonic release
# binary: python:*-slim lacks them and Tectonic then dies at exec with a
# shared-library loader error (instant compile "failure" with no TeX log).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libcairo2 libfontconfig1 libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with a writable $HOME (Tectonic binary + LaTeX package caches
# live under ~/.cache).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user:user . ./src
RUN pip install --no-cache-dir --user "./src[gui,cairo]" && rm -rf ./src

# Bake the Tectonic binary and warm its LaTeX package cache into the image --
# essential on the 0.1-vCPU free instance, where downloading the bundle at
# request time would blow straight through any sane HTTP timeout. Best-effort:
# anything missing is still fetched on demand at runtime.
COPY --chown=user:user deploy/hf-space/warm_cache.py .
RUN python warm_cache.py

# The demo entry point hardens the server (rate limit, upload cap, disabled
# filesystem export, privacy banner) -- see latextify/gui/demo.py. Render
# injects PORT and routes to it; the entry point picks it up automatically.
ENV LATEXTIFY_DEMO_HOST=0.0.0.0
CMD ["python", "-m", "latextify.gui.demo"]
