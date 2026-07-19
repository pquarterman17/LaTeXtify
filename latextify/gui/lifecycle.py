"""Auto-exit the local ``latextify gui`` process once every browser tab closes.

``latextify gui`` used to run until the user hit Ctrl+C in the terminal it
was launched from, even after the browser window it opened was closed. This
module lets the server notice that and shut itself down, so closing the tab
is enough.

No WebSocket library is available -- ``latextify gui`` depends on plain
``uvicorn`` (not the ``[standard]`` extra), so this is a plain HTTP
heartbeat instead of a push channel:

- The served page (``static/lifecycle.js``) POSTs its random tab id to
  ``/api/heartbeat`` every few seconds, and fires a ``navigator.sendBeacon``
  to ``/api/tab-closed`` on ``pagehide`` (fired reliably on tab close and
  navigation, unlike a ``fetch`` from the same handler).
- :func:`start_client_monitor` polls ``app.state.active_tabs`` in the
  background and calls ``app.state.shutdown()`` once every tab is gone.

Only opted into by the local CLI launcher's default (:func:`latextify.cli.gui`
passes ``auto_shutdown=True`` unless ``--keep-alive`` is given) -- the hosted
demo (:mod:`latextify.gui.demo`) never enables it, and a bare
``create_app()`` starts no monitor, so nothing here changes behaviour unless
a caller opts in. This mirrors the local-server-hardening rule of wiring
shutdown to the app's own lifecycle rather than ``atexit``: the monitor is an
``asyncio`` task started from the FastAPI lifespan and cancelled on shutdown,
never a background thread the interpreter has to be tricked into joining.

Timing (all three matter together -- see the reasoning below each):

- ``stale_after`` (default 120s): a tab that goes this long without a
  heartbeat is dropped as a crash/kill fallback. It MUST exceed the ~60s
  timer-throttle browsers apply to backgrounded tabs, so a tab the user has
  merely switched away from (heartbeat throttled, not closed) is never
  reaped as if it had closed.
- ``poll_interval`` (default 2s): how often the monitor re-checks state.
- ``reload_grace`` (default 5s): once the last tab disappears, the monitor
  waits this long and rechecks before actually shutting down. A page reload
  fires ``pagehide`` (removing the old tab id) and then re-registers a new
  tab id almost immediately -- the grace window is what stops that from
  reading as "last tab closed."

``register_lifecycle`` and ``start_client_monitor`` are split apart: the
heartbeat/tab-closed routes are always registered (so the page can always
send them without knowing whether auto-shutdown is active), while the
monitor task -- the only thing that can actually stop the server -- is
started only when a caller explicitly asks for it.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Depends, FastAPI, Request

from latextify.gui.guard import require_gui_auth

#: See the timing discussion in the module docstring.
_STALE_AFTER_SECONDS = 120.0
_POLL_INTERVAL_SECONDS = 2.0
_RELOAD_GRACE_SECONDS = 5.0


def register_lifecycle(app: FastAPI) -> None:
    """Attach the tab-heartbeat routes and their backing state to ``app``.

    Safe to call unconditionally: without a running
    :func:`start_client_monitor` task, the routes just record state that
    nothing ever reads.
    """
    app.state.active_tabs: dict[str, float] = {}
    app.state.client_ever_seen = False

    @app.post(
        "/api/heartbeat",
        status_code=204,
        include_in_schema=False,
        dependencies=[Depends(require_gui_auth)],
    )
    async def heartbeat(request: Request) -> None:
        tab_id = (await request.body()).decode("utf-8", errors="replace").strip()
        if tab_id:
            app.state.active_tabs[tab_id] = time.monotonic()
            app.state.client_ever_seen = True

    @app.post("/api/tab-closed", status_code=204, include_in_schema=False)
    async def tab_closed(request: Request) -> None:
        # Deliberately NOT behind require_gui_auth: navigator.sendBeacon
        # cannot set custom headers, so it can't carry the CSRF secret. This
        # is low-impact to leave open: the endpoint is loopback-only, keyed
        # by an unguessable crypto.randomUUID tab id, and the worst a forged
        # call can do is drop one tab from the active set -- at most
        # triggering the same local auto-shutdown the user could already do
        # with Ctrl+C.
        tab_id = (await request.body()).decode("utf-8", errors="replace").strip()
        app.state.active_tabs.pop(tab_id, None)


def _prune_stale_tabs(
    active_tabs: dict[str, float], *, now: float, stale_after: float = _STALE_AFTER_SECONDS
) -> None:
    """Drop tabs whose most recent heartbeat is older than the stale threshold."""
    dead = [tab for tab, last in active_tabs.items() if now - last > stale_after]
    for tab in dead:
        del active_tabs[tab]


def start_client_monitor(
    app: FastAPI,
    *,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    stale_after: float = _STALE_AFTER_SECONDS,
    reload_grace: float = _RELOAD_GRACE_SECONDS,
) -> asyncio.Task[None]:
    """Start the background task that shuts the server down after the last
    browser tab closes.

    The interval/threshold keyword arguments default to this module's
    constants and exist mainly so tests can drive the real loop with tiny
    values instead of waiting out the production timings. Never shuts down
    before ``app.state.client_ever_seen`` is true -- a slow or failed
    browser launch must leave the server running, exactly as it does today
    without this feature.
    """

    async def _loop() -> None:
        while True:
            await asyncio.sleep(poll_interval)
            _prune_stale_tabs(app.state.active_tabs, now=time.monotonic(), stale_after=stale_after)
            if not app.state.client_ever_seen or app.state.active_tabs:
                continue
            # The last known tab just vanished. Wait out the reload grace
            # and recheck before treating it as a real close (see module
            # docstring).
            await asyncio.sleep(reload_grace)
            _prune_stale_tabs(app.state.active_tabs, now=time.monotonic(), stale_after=stale_after)
            if not app.state.active_tabs:
                app.state.shutdown()
                return

    return asyncio.create_task(_loop())
