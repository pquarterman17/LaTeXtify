"""Request protection for the mutating localhost GUI endpoints (audit item 4).

Binding to ``127.0.0.1`` stops remote machines from reaching the server, but it
does **not** stop a malicious web page the user is visiting from scripting
requests to ``http://127.0.0.1:8000`` in the background -- triggering expensive
conversions or the native folder picker (CSRF against a localhost service), and
DNS-rebinding can defeat the origin assumptions a naive check would make.

This module adds a dependency-light, local-only defence with three independent
layers, all required on mutating ``/api/*`` requests:

1. **Per-process secret.** ``new_gui_secret()`` mints a random token at startup;
   the served page is the only thing that learns it (:func:`inject_gui_secret`
   injects a tiny ``fetch`` wrapper that attaches it as a request header). A
   cross-origin attacker page can *send* requests but the same-origin policy
   stops it from *reading* our page, so it never learns the secret.
2. **Host allow-list.** The ``Host`` header must be a loopback literal, which
   defeats DNS rebinding (a rebound ``evil.com`` -> 127.0.0.1 still sends
   ``Host: evil.com``).
3. **Origin allow-list.** A present ``Origin`` must be loopback; a null/absent
   Origin (same-origin navigations, curl, tests) is allowed.

The secret travels in a header, never a URL or log line, and is compared in
constant time. Artifact ``GET`` endpoints stay unprotected bearer-token
capabilities -- they hand out only what a prior authorized convert produced.
"""

from __future__ import annotations

import json
import secrets

from fastapi import HTTPException, Request

#: Request header the injected fetch wrapper attaches the per-process secret to.
SECRET_HEADER = "X-LaTeXtify-Secret"

#: Bare host literals treated as loopback (after port/bracket stripping).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def new_gui_secret() -> str:
    """Mint a fresh per-process GUI secret."""
    return secrets.token_urlsafe(32)


def _bare_host(host_header: str) -> str:
    """Strip a ``:port`` and any IPv6 brackets from a Host/Origin authority."""
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1] or [::1]:port
        end = h.find("]")
        return h[1:end] if end != -1 else h[1:]
    if h.count(":") == 1:  # ipv4/hostname with a port (bare ::1 has 2+ colons)
        return h.split(":", 1)[0]
    return h


def _host_is_loopback(host_header: str | None) -> bool:
    if not host_header:
        return False
    return _bare_host(host_header) in _LOOPBACK_HOSTS


def _origin_host(origin: str) -> str:
    """Bare host of an ``Origin`` (``scheme://host[:port]``); "" when malformed."""
    scheme, sep, authority = origin.partition("://")
    if not sep or not authority:
        return ""
    # An Origin has no path, but guard against one anyway before host parsing.
    return _bare_host(authority.split("/", 1)[0])


def _origin_is_loopback(origin: str) -> bool:
    """True if an ``Origin`` (``scheme://host[:port]``) is a loopback address."""
    return _origin_host(origin) in _LOOPBACK_HOSTS


def require_gui_auth(request: Request) -> None:
    """FastAPI dependency: reject a mutating request that fails any auth layer.

    Raises 403 (never revealing which layer failed) unless the Host is
    loopback, any present Origin is loopback, and the per-process secret header
    matches. Attach with ``dependencies=[Depends(require_gui_auth)]`` so the
    endpoint signature is untouched.

    Hosted-demo mode (``app.state.demo_mode``, see :mod:`latextify.gui.demo`)
    is legitimately served from a public hostname, so the loopback layers are
    replaced by a same-origin check: any present ``Origin`` must name the same
    host the request was addressed to. The secret layer is unchanged -- a
    cross-origin attacker page still cannot read the served page to learn it,
    so the CSRF defence holds on the public deployment too.
    """
    if getattr(request.app.state, "demo_mode", False):
        origin = request.headers.get("origin")
        if origin is not None and _origin_host(origin) != _bare_host(
            request.headers.get("host") or ""
        ):
            raise HTTPException(status_code=403, detail="forbidden")
    else:
        if not _host_is_loopback(request.headers.get("host")):
            raise HTTPException(status_code=403, detail="forbidden")

        origin = request.headers.get("origin")
        if origin is not None and not _origin_is_loopback(origin):
            raise HTTPException(status_code=403, detail="forbidden")

    expected = getattr(request.app.state, "gui_secret", None)
    provided = request.headers.get(SECRET_HEADER, "")
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="forbidden")


def inject_gui_secret(html: str, secret: str) -> str:
    """Inject a ``fetch`` wrapper that attaches the secret to ``/api/`` requests.

    The wrapper is added just inside ``<head>`` so it runs before the page's
    own script issues any request. It only touches same-origin ``/api/`` URLs,
    so it never leaks the secret to a third-party endpoint. The secret is
    embedded as a JSON string literal, so it cannot break out of the JS context.
    """
    literal = json.dumps(secret)
    wrapper = (
        "<script>(function(){"
        f"var S={literal};var f=window.fetch;"
        'window.fetch=function(i,o){o=o||{};'
        'var u=(typeof i==="string")?i:(i&&i.url)||"";'
        'if(u.indexOf("/api/")>-1){'
        'o.headers=Object.assign({},o.headers,{"' + SECRET_HEADER + '":S});}'
        "return f.call(this,i,o);};})();</script>"
    )
    return html.replace("<head>", "<head>" + wrapper, 1)
