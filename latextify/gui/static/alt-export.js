/* LaTeXtify GUI — standalone "Export to HTML/Markdown" panel (served at
   /static/alt-export.js). Independent of the main conversion flow: uploads
   one manuscript plus a chosen format to POST /api/export-format and offers
   the produced file for download. Uses window.LTXApp for the shared error
   helpers set up by app.js. Buildless vanilla JS (mirrors clean.js). */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const fileInput = el("altexport-file");
  const formatSelect = el("altexport-format");
  const runBtn = el("altexport-btn");
  const statusEl = el("altexport-status");
  const reportEl = el("altexport-report");

  fileInput.addEventListener("change", () => {
    runBtn.disabled = !fileInput.files.length;
    statusEl.textContent = "";
    reportEl.classList.add("hidden");
    reportEl.innerHTML = "";
  });

  function summarize(body) {
    const parts = [
      body.figure_count + " figure(s)",
      body.citation_count + " reference(s)",
    ];
    return parts.join(", ") + ".";
  }

  async function runExport() {
    const file = fileInput.files[0];
    if (!file) return;
    window.LTXApp.clearError();
    runBtn.disabled = true;
    statusEl.textContent = "Exporting…";
    reportEl.classList.add("hidden");
    reportEl.innerHTML = "";
    try {
      const fd = new FormData();
      fd.append("main", file);
      fd.append("fmt", formatSelect.value);
      const resp = await fetch("/api/export-format", { method: "POST", body: fd });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "export failed (" + resp.status + ")");

      statusEl.textContent = "Done.";
      reportEl.textContent = summarize(body) + " ";
      const link = document.createElement("a");
      link.href = body.download_url;
      link.textContent = "⬇ Download " + (body.format === "html" ? ".html" : ".md");
      link.setAttribute("download", "");
      reportEl.appendChild(link);
      if (body.warnings && body.warnings.length) {
        const list = document.createElement("ul");
        list.className = "warnings-list";
        body.warnings.forEach((message) => {
          const li = document.createElement("li");
          li.textContent = message;
          list.appendChild(li);
        });
        reportEl.appendChild(list);
      }
      reportEl.classList.remove("hidden");
    } catch (err) {
      statusEl.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      runBtn.disabled = !fileInput.files.length;
    }
  }
  runBtn.addEventListener("click", runExport);
})();
