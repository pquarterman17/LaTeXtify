/* LaTeXtify GUI — standalone "Check & clean a file" panel (served at
   /static/clean.js). Independent of the main conversion flow. Two actions on
   one upload:

     Check   -> POST /api/inspect     (writes nothing, no download)
     Clean   -> POST /api/clean-file  (returns a sanitized copy)

   The panel deliberately renders `removable: false` findings differently.
   A user who sees "3 things removed" and a green tick will assume the file is
   safe; if a PDF still has selectable text under a redaction box, or a
   workbook still has its hidden sheet, that assumption is the whole risk.
   Those are shown as "you must fix this yourself" and never counted as
   removed.

   Uses window.LTXApp for the shared error helpers set up by app.js.
   Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const fileInput = el("clean-file");
  const cleanBtn = el("clean-btn");
  const inspectBtn = el("inspect-btn");
  const statusEl = el("clean-status");
  const reportEl = el("clean-report");

  const SEVERITY_LABEL = { high: "High", medium: "Medium", low: "Low" };

  function reset() {
    statusEl.textContent = "";
    reportEl.classList.add("hidden");
    reportEl.replaceChildren();
  }

  fileInput.addEventListener("change", () => {
    const has = fileInput.files.length > 0;
    cleanBtn.disabled = !has;
    inspectBtn.disabled = !has;
    reset();
  });

  function findingRow(finding) {
    const row = document.createElement("li");
    row.className = "finding finding-" + finding.severity;

    const badge = document.createElement("span");
    badge.className = "finding-severity";
    badge.textContent = SEVERITY_LABEL[finding.severity] || finding.severity;
    row.appendChild(badge);

    const summary = document.createElement("span");
    summary.className = "finding-summary";
    summary.textContent = finding.summary;
    row.appendChild(summary);

    if (!finding.removable) {
      const flag = document.createElement("span");
      flag.className = "finding-manual";
      flag.textContent = "cannot be fixed automatically";
      row.appendChild(flag);
    }

    const detail = document.createElement("p");
    detail.className = "finding-detail";
    detail.textContent = finding.detail;
    row.appendChild(detail);
    return row;
  }

  function renderList(title, findings) {
    const heading = document.createElement("h4");
    heading.textContent = title;
    reportEl.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "finding-list";
    findings.forEach((f) => list.appendChild(findingRow(f)));
    reportEl.appendChild(list);
  }

  function renderWarnings(warnings) {
    if (!warnings || !warnings.length) return;
    const heading = document.createElement("h4");
    heading.textContent = "Still needs your attention";
    reportEl.appendChild(heading);
    const list = document.createElement("ul");
    list.className = "finding-list warning-list";
    warnings.forEach((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      list.appendChild(item);
    });
    reportEl.appendChild(list);
  }

  async function post(url, extra) {
    const file = fileInput.files[0];
    const fd = new FormData();
    fd.append("main", file);
    if (extra) Object.keys(extra).forEach((k) => fd.append(k, extra[k]));
    const resp = await fetch(url, { method: "POST", body: fd });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || "request failed (" + resp.status + ")");
    return body;
  }

  function busy(on) {
    const has = fileInput.files.length > 0;
    cleanBtn.disabled = on || !has;
    inspectBtn.disabled = on || !has;
  }

  async function runInspect() {
    if (!fileInput.files.length) return;
    window.LTXApp.clearError();
    busy(true);
    statusEl.textContent = "Checking…";
    reset();
    statusEl.textContent = "Checking…";
    try {
      const body = await post("/api/inspect");
      const findings = body.findings || [];
      statusEl.textContent = findings.length
        ? body.file_format + ": " + findings.length + " finding(s)"
        : body.file_format + ": nothing found";
      if (findings.length) renderList("Found in this file", findings);
      renderWarnings(body.warnings);
      reportEl.classList.remove("hidden");
    } catch (err) {
      statusEl.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      busy(false);
    }
  }

  async function runClean() {
    if (!fileInput.files.length) return;
    window.LTXApp.clearError();
    busy(true);
    statusEl.textContent = "Cleaning…";
    reset();
    statusEl.textContent = "Cleaning…";
    try {
      const keepNotes = el("clean-keep-notes");
      const body = await post("/api/clean-file", {
        keep_notes: keepNotes && keepNotes.checked ? "true" : "false",
      });
      const removed = body.removed || [];
      statusEl.textContent = "Done — " + body.file_format + ".";

      if (removed.length) renderList("Removed", removed);
      else {
        const none = document.createElement("p");
        none.textContent = "Nothing needed removing.";
        reportEl.appendChild(none);
      }
      renderWarnings(body.warnings);

      const link = document.createElement("a");
      link.href = body.clean_url;
      link.textContent = "⬇ Download cleaned file";
      link.setAttribute("download", "");
      link.className = "clean-download";
      reportEl.appendChild(link);
      reportEl.classList.remove("hidden");
    } catch (err) {
      statusEl.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      busy(false);
    }
  }

  inspectBtn.addEventListener("click", runInspect);
  cleanBtn.addEventListener("click", runClean);
})();
