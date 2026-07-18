"""Hosted-demo hardening for the GUI server (Hugging Face Space deployment).

The GUI was designed as a *local* tool -- ``latextify gui`` binds 127.0.0.1 and
trusts its single user. A public demo (e.g. a Hugging Face Space) inherits none
of that trust, so ``create_app(demo=True)`` swaps in the policies defined here:

- **Filesystem endpoints disabled.** ``/api/pick-folder`` and ``/api/export``
  write to paths on the *server* host; in a container that is meaningless at
  best and a write-anywhere primitive at worst. Demo users download the PDF/ZIP
  instead. The UI's Export panel is hidden by the injected banner script.
- **Smaller upload cap.** 25 MB per file instead of the local 250 MB.
- **Per-client rate limit.** Conversions run Pandoc + Tectonic (tens of CPU
  seconds each); a sliding-window limit keyed by client IP keeps one visitor
  from monopolizing the shared Space. The Space's proxy terminates TLS and
  forwards the real client in ``X-Forwarded-For``.
- **Privacy banner.** Injected server-side into the served page (the on-disk
  ``index.html`` is untouched, keeping it under its size-ratchet pin) so every
  visitor sees what happens to an uploaded manuscript before choosing one.

Auth note: the per-process secret CSRF defence (:mod:`latextify.gui.guard`)
still applies in demo mode; only the *loopback* Host/Origin checks are replaced
by a same-origin check, because a hosted page is legitimately served from a
public hostname. Requests are otherwise as untrusted as any internet traffic.

Concurrency note: the conversion handlers do their heavy work on the event
loop, so requests naturally serialize -- a crude but real bound on concurrent
Tectonic compiles. The rate limit bounds how much work one client can queue.

Run the demo server directly (the Space's Dockerfile CMD)::

    python -m latextify.gui.demo

Binds ``LATEXTIFY_DEMO_HOST`` (default 127.0.0.1 -- the container sets 0.0.0.0)
on port ``LATEXTIFY_DEMO_PORT`` (default 7860, the HF Spaces convention).
"""

from __future__ import annotations

import os
import time
from collections import deque

from fastapi import HTTPException, Request

#: Demo per-file upload cap (local default is 250 MB; a demo needs far less).
DEMO_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

#: Sliding-window rate limit for conversion endpoints, per client key.
DEMO_RATE_LIMIT_REQUESTS = 10
DEMO_RATE_LIMIT_WINDOW_SECONDS = 3600.0  # 10 conversions per hour per client


class RateLimiter:
    """Sliding-window request limiter keyed by an opaque client string.

    Purely in-memory (a demo Space is a single process); empty windows are
    dropped on every check so the key map cannot grow without bound.
    """

    def __init__(
        self,
        max_requests: int = DEMO_RATE_LIMIT_REQUESTS,
        window_seconds: float = DEMO_RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        """Seconds until ``key`` may make another request; 0.0 means allowed.

        A return of 0.0 *records* the request against the window (check and
        consume are one operation, so racing requests cannot both pass on the
        last slot).
        """
        t = time.monotonic() if now is None else now
        cutoff = t - self.window_seconds
        window = self._hits.get(key)
        if window is None:
            window = self._hits[key] = deque()
        while window and window[0] <= cutoff:
            window.popleft()
        # Drop other keys' empty windows so the map stays bounded by active clients.
        for k in [k for k, w in self._hits.items() if not w and k != key]:
            del self._hits[k]
        if len(window) >= self.max_requests:
            return window[0] + self.window_seconds - t
        window.append(t)
        return 0.0


def client_key(request: Request) -> str:
    """Best-effort client identity: first ``X-Forwarded-For`` hop, else peer IP.

    Behind the Space's reverse proxy the peer IP is the proxy itself, so the
    forwarded header is the only per-visitor signal available. A forged header
    only lets a client rate-limit *itself* under a different key, which the
    per-request cost cap tolerates.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def require_demo_rate_limit(request: Request) -> None:
    """FastAPI dependency: 429 a conversion request over the demo rate limit.

    No-op outside demo mode (``app.state.rate_limiter`` is ``None``), so the
    local tool is completely unaffected.
    """
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    wait = limiter.retry_after(client_key(request))
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=(
                "demo rate limit reached "
                f"({limiter.max_requests} conversions per hour) -- try again later, "
                "or install LaTeXtify locally for unlimited use."
            ),
            headers={"Retry-After": str(max(1, int(wait)))},
        )


#: Banner + Export-panel hiding, injected just after <body> of the served page.
#: Inline styles only -- the static page's stylesheet knows nothing about it.
_DEMO_BANNER = (
    '<div id="demo-banner" style="background:#fff8e6;border-bottom:1px solid #f0d585;'
    'color:#1c2128;padding:0.6rem 2rem;font-size:0.85rem;line-height:1.4;">'
    "<strong>Public demo.</strong> Uploaded files are processed on a shared server "
    "and deleted within an hour, but treat this as a demo: do <strong>not</strong> "
    "upload confidential or unpublished manuscripts you would not email to a "
    "stranger. For private conversions, "
    '<a href="https://github.com/pquarterman17/LaTeXtify">install LaTeXtify '
    "locally</a> &mdash; it runs entirely on your machine. Uploads are capped at "
    "25&nbsp;MB per file and conversions are rate-limited."
    "</div>"
    "<script>window.LATEXTIFY_DEMO=true;"
    "document.addEventListener('DOMContentLoaded',function(){"
    "['export-dir','browse-btn'].forEach(function(id){"
    "var n=document.getElementById(id);"
    "var p=n&&n.closest?n.closest('.panel'):null;"
    "if(p){p.classList.add('hidden');}});});</script>"
)


def inject_demo_banner(html: str) -> str:
    """Insert the demo privacy banner and Export-panel hiding into the page."""
    return html.replace("<body>", "<body>" + _DEMO_BANNER, 1)


def serve_demo() -> None:  # pragma: no cover - thin uvicorn launcher
    """Run the demo server (``python -m latextify.gui.demo``).

    Host/port come from ``LATEXTIFY_DEMO_HOST`` / ``LATEXTIFY_DEMO_PORT``. The
    host default stays loopback so a curious local run exposes nothing; the
    Space's Dockerfile sets ``LATEXTIFY_DEMO_HOST=0.0.0.0`` explicitly.
    """
    import uvicorn

    from latextify.gui.server import create_app  # deferred: avoids import cycle

    host = os.environ.get("LATEXTIFY_DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("LATEXTIFY_DEMO_PORT", "7860"))
    print(f"LaTeXtify demo GUI on http://{host}:{port} (demo hardening active)")
    uvicorn.run(create_app(demo=True), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    serve_demo()
