/* LaTeXtify GUI — tab lifecycle heartbeat (served at /static/lifecycle.js).
   Lets the local `latextify gui` launcher notice when every browser tab
   showing this page has closed, so it can exit its terminal process
   automatically instead of waiting for Ctrl+C. Server side of this contract
   lives in latextify/gui/lifecycle.py.

   No WebSocket library is available (`latextify gui` depends on plain
   uvicorn, not the [standard] extra), so this is a periodic HTTP heartbeat
   rather than a push channel. Plain window.fetch is already patched by the
   secret-injecting wrapper the server embeds in this page (see
   latextify/gui/guard.py::inject_gui_secret, which rewrites window.fetch to
   attach the CSRF header to every same-origin /api/ request) — a bare
   fetch() call below carries it with no extra wiring. */
(function () {
  "use strict";

  const TAB_ID = crypto.randomUUID();
  const HEARTBEAT_INTERVAL_MS = 4000;

  function sendHeartbeat() {
    fetch("/api/heartbeat", { method: "POST", body: TAB_ID }).catch(function () {
      // A dropped heartbeat is not fatal: the server only reaps a tab after
      // ~120s of silence, far past one lost request.
    });
  }

  sendHeartbeat();
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);

  // sendBeacon is the reliable signal for "the tab is going away" (close or
  // navigation) — unlike fetch, it is not cancelled when the page unloads.
  // It cannot carry custom headers, so /api/tab-closed is intentionally
  // unauthenticated; see latextify/gui/lifecycle.py for why that's safe.
  window.addEventListener("pagehide", function () {
    navigator.sendBeacon("/api/tab-closed", TAB_ID);
  });
})();
