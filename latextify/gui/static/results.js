/* LaTeXtify GUI — result panels (split from app.js; served at
   /static/results.js): warnings, downloads, PDF preview tabs, report.md.
   Exposes window.LTXResults; app.js drives it after each conversion, and
   review.js/export.js reach it through the window.LTXApp forwarding bridge.
   Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const resultsPlaceholder = el("results-placeholder");
  const warningsPanel = el("warnings-panel");
  const warningsList = el("warnings-list");
  const downloadsPanel = el("downloads-panel");
  const downloads = el("downloads");
  const pdfPanel = el("pdf-panel");
  const pdfTabs = el("pdf-tabs");
  const pdfEmbed = el("pdf-embed");
  const reportPanel = el("report-panel");
  const reportText = el("report-text");

  // The right (outputs) column shows this empty-state prompt until the first
  // conversion produces something to look at; app.js hides it right after a
  // successful convert/apply-corrections response, before rendering results.
  function hidePlaceholder() { resultsPlaceholder.classList.add("hidden"); }

  // Warnings accumulate across convert + export flows in one shared panel.
  function appendWarning(message) {
    const li = document.createElement("li");
    li.textContent = message;
    warningsList.appendChild(li);
    warningsPanel.classList.remove("hidden");
  }

  function reset() {
    resultsPlaceholder.classList.remove("hidden");
    warningsPanel.classList.add("hidden"); warningsList.innerHTML = "";
    downloadsPanel.classList.add("hidden"); downloads.innerHTML = "";
    reportPanel.classList.add("hidden"); reportText.textContent = "";
    pdfPanel.classList.add("hidden"); pdfTabs.innerHTML = ""; pdfEmbed.removeAttribute("src");
  }

  function renderPdfTabs(body) {
    pdfTabs.innerHTML = "";  // rebuildable: also called to refresh after a recompile
    const views = [
      ["Main", body.pdf_url],
      ["Supplement", body.supplement_pdf_url],
      ["Combined", body.combined_pdf_url],
      ["Equation audit", body.audit_pdf_url],
    ].filter((v) => v[1]);
    if (!views.length) return;

    function show(url, btn) {
      pdfEmbed.setAttribute("src", url);
      Array.from(pdfTabs.children).forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
    }
    views.forEach(([label, url], i) => {
      const btn = document.createElement("button");
      btn.type = "button"; btn.textContent = label;
      btn.addEventListener("click", () => show(url, btn));
      pdfTabs.appendChild(btn);
      if (i === 0) show(url, btn);
    });
    pdfPanel.classList.remove("hidden");
  }

  function renderDownloads(body) {
    const items = [
      ["Project .zip", body.zip_url],
      ["combined.pdf", body.combined_pdf_url],
      ["audit.pdf", body.audit_pdf_url],
    ].filter((v) => v[1]);
    if (!items.length) return;
    items.forEach(([label, url]) => {
      const a = document.createElement("a");
      a.href = url; a.textContent = "⬇ " + label; a.setAttribute("download", "");
      downloads.appendChild(a);
    });
    downloadsPanel.classList.remove("hidden");
  }

  function showReport(markdown) {
    if (!markdown) return;
    reportText.textContent = markdown;
    reportPanel.classList.remove("hidden");
  }

  window.LTXResults = {
    reset: reset,
    renderPdfTabs: renderPdfTabs,
    renderDownloads: renderDownloads,
    appendWarning: appendWarning,
    showReport: showReport,
    hidePlaceholder: hidePlaceholder,
  };
})();
