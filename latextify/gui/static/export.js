/* LaTeXtify GUI — export-to-folder panel (split from app.js; served at
   /static/export.js). Uses window.LTXApp for the preview token and shared
   status/error helpers, and exposes window.LTXExport so app.js can refresh
   the Export button when the preview token changes. Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const exportDir = el("export-dir");
  const browseBtn = el("browse-btn");
  const exportBtn = el("export-btn");
  const exportStatus = el("export-status");
  // Export checkbox id suffix -> the export_types value the server expects.
  const EXPORT_TYPES = {
    "exp-project": "project", "exp-main_pdf": "main_pdf", "exp-supplement_pdf": "supplement_pdf",
    "exp-combined_pdf": "combined_pdf", "exp-audit_pdf": "audit_pdf", "exp-zip": "zip",
  };

  // Export is only allowed after a successful preview, with a folder chosen and
  // at least one artifact ticked.
  function updateExportButton() {
    const token = window.LTXApp.exportToken();
    const dest = exportDir.value.trim();
    const anyType = Object.keys(EXPORT_TYPES).some((id) => el(id).checked);
    exportBtn.disabled = !(token && dest && anyType);
    exportBtn.title = token ? "" : "Preview a conversion first";
  }

  async function browseFolder() {
    browseBtn.disabled = true;
    const previous = browseBtn.textContent;
    browseBtn.textContent = "Opening…";
    try {
      const resp = await fetch("/api/pick-folder", { method: "POST" });
      const body = await resp.json();
      if (resp.ok && body.path) exportDir.value = body.path;
    } catch (err) {
      // A headless host has no dialog; the text field is the fallback.
      window.LTXApp.showError("Folder picker unavailable — type a path instead: " + err.message);
    } finally {
      browseBtn.disabled = false;
      browseBtn.textContent = previous;
      updateExportButton();
    }
  }
  browseBtn.addEventListener("click", browseFolder);

  // The Export button only lights up with a fresh preview + a folder + a ticked box.
  exportDir.addEventListener("input", updateExportButton);
  Object.keys(EXPORT_TYPES).forEach((id) => el(id).addEventListener("change", updateExportButton));

  async function runExport() {
    const token = window.LTXApp.exportToken();
    if (!token) {
      window.LTXApp.showError("Preview a conversion first, then export.");
      return;
    }
    const dest = exportDir.value.trim();
    const types = Object.entries(EXPORT_TYPES)
      .filter(([id]) => el(id).checked)
      .map(([, value]) => value);
    if (!dest || !types.length) {
      window.LTXApp.showError("Pick a destination folder and at least one file type to export.");
      return;
    }
    window.LTXApp.clearError();
    exportBtn.disabled = true;
    exportStatus.textContent = "Exporting…";
    try {
      const resp = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ export_token: token, export_dir: dest, export_types: types }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "export failed (" + resp.status + ")");
      const what = body.exported && body.exported.length ? body.exported.join(", ") : "nothing";
      exportStatus.textContent = "Exported " + what + " to " + body.exported_to;
      (body.warnings || []).forEach((message) => window.LTXApp.appendWarning(message));
    } catch (err) {
      exportStatus.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      updateExportButton();
    }
  }
  exportBtn.addEventListener("click", runExport);

  window.LTXExport = { update: updateExportButton };
})();
