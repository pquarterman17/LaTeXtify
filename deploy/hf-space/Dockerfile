# Hugging Face Space (Docker SDK) image for the LaTeXtify public demo.
#
# The Space repo holds only this Dockerfile + README.md + warm_cache.py; the
# application itself is installed from a pinned GitHub ref, so deploying a new
# version is "bump LATEXTIFY_REF, push" (see deploy/hf-space/DEPLOY.md in the
# main repo).
FROM python:3.12-slim

# libcairo2 lets cairosvg (the preferred SVG->PDF converter) load its native
# backend; latextify falls back to pure-Python svglib without it, so this is a
# fidelity upgrade, not a requirement.
# The rest are hard requirements of the Tectonic release binary, which links
# them dynamically: python:*-slim lacks them and Tectonic then dies at exec
# with a shared-library loader error (instant compile "failure", no TeX log).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 libfontconfig1 libgraphite2-3 libharfbuzz0b libssl3 \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces convention: run as uid 1000 with a writable $HOME (the Tectonic
# binary + LaTeX package caches live under ~/.cache).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

# The LaTeXtify ref to deploy. Bump to a newer tag/commit and push to rebuild;
# a moving branch name alone will NOT rebuild (Docker caches the layer), so
# prefer an exact commit SHA or tag.
ARG LATEXTIFY_REF=main
RUN pip install --no-cache-dir --user \
    "latextify[gui,cairo] @ https://github.com/pquarterman17/LaTeXtify/archive/${LATEXTIFY_REF}.tar.gz"

# Bake the Tectonic binary and warm its LaTeX package cache into the image so
# the first visitor's conversion compiles in seconds instead of paying the
# bundle download. Best-effort: anything missing is fetched on demand at runtime.
COPY --chown=user:user warm_cache.py .
RUN python warm_cache.py

# The demo entry point hardens the server (rate limit, upload cap, disabled
# filesystem export, privacy banner) -- see latextify/gui/demo.py.
ENV LATEXTIFY_DEMO_HOST=0.0.0.0 \
    LATEXTIFY_DEMO_PORT=7860
EXPOSE 7860
CMD ["python", "-m", "latextify.gui.demo"]
