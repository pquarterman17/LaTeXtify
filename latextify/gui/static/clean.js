/* LaTeXtify GUI — standalone "Clean a .docx" panel (served at
   /static/clean.js). Independent of the main conversion flow: uploads one
   .docx to POST /api/clean-docx and offers the sanitized copy for download.
   Uses window.LTXApp for the shared error helpers set up by app.js.
   Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const fileInput = el("clean-file");
  const cleanBtn = el("clean-btn");
  const statusEl = el("clean-status");
  const reportEl = el("clean-report");

  fileInput.addEventListener("change", () => {
    cleanBtn.disabled = !fileInput.files.length;
    statusEl.textContent = "";
    reportEl.classList.add("hidden");
    reportEl.innerHTML = "";
  });

  function summarize(report) {
    const parts = [
      report.tracked_changes_accepted + " tracked change(s) accepted",
      report.comments_removed + " comment(s) removed",
      report.hidden_runs_removed + " hidden run(s) removed",
      report.docprops_stripped ? "metadata stripped" : "no metadata found",
    ];
    if (report.rsids_scrubbed) parts.push("rsids scrubbed");
    return parts.join(", ") + ".";
  }

  async function runClean() {
    const file = fileInput.files[0];
    if (!file) return;
    window.LTXApp.clearError();
    cleanBtn.disabled = true;
    statusEl.textContent = "Cleaning…";
    reportEl.classList.add("hidden");
    reportEl.innerHTML = "";
    try {
      const fd = new FormData();
      fd.append("main", file);
      const resp = await fetch("/api/clean-docx", { method: "POST", body: fd });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "clean failed (" + resp.status + ")");

      statusEl.textContent = "Done.";
      reportEl.textContent = summarize(body) + " ";
      const link = document.createElement("a");
      link.href = body.clean_url;
      link.textContent = "⬇ Download cleaned .docx";
      link.setAttribute("download", "");
      reportEl.appendChild(link);
      reportEl.classList.remove("hidden");
    } catch (err) {
      statusEl.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      cleanBtn.disabled = !fileInput.files.length;
    }
  }
  cleanBtn.addEventListener("click", runClean);
})();
