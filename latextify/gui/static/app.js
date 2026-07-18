/* LaTeXtify GUI — main application logic (split from index.html; served at
   /static/app.js). Buildless vanilla JS: no imports, no build step. The
   reference-review panel lives in review.js; the two files talk through the
   window.LTXApp / window.LTXReview namespaces (load order does not matter —
   each is only dereferenced inside event handlers). */
(function () {
  "use strict";

  const ROLES = ["main", "supplement", "figure", "references", "ignore"];
  const ROLE_LABELS = {
    main: "Main text", supplement: "Supplement", figure: "Figure",
    references: "References (.bib)", ignore: "Ignore",
  };
  const IMAGE_EXTS = ["png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "webp", "eps", "svg", "pdf"];
  const REF_EXTS = ["bib", "ris"];
  const CITATION_MODE_LABELS = { numeric: "numeric — [1], [2]", authoryear: "author–year — (Doe, 2020)" };
  const el = (id) => document.getElementById(id);
  const dropzone = el("dropzone");
  const fileInput = el("file-input");
  const filelist = el("filelist");
  const filelistBody = el("filelist-body");
  const journalSelect = el("journal-select");
  const citationSelect = el("citation-select");
  const crossrefEmail = el("crossref-email");
  const optPdf = el("opt-pdf"), optCombine = el("opt-combine"), optSi1col = el("opt-si1col"),
    optZip = el("opt-zip"), optNoFigs = el("opt-nofigs"), optAudit = el("opt-audit"), optCheckRefs = el("opt-checkrefs");
  const exportDir = el("export-dir");
  const browseBtn = el("browse-btn");
  const exportBtn = el("export-btn");
  const exportStatus = el("export-status");
  // Export checkbox id suffix -> the export_types value the server expects.
  const EXPORT_TYPES = {
    "exp-project": "project", "exp-main_pdf": "main_pdf", "exp-supplement_pdf": "supplement_pdf",
    "exp-combined_pdf": "combined_pdf", "exp-audit_pdf": "audit_pdf", "exp-zip": "zip",
  };
  const convertBtn = el("convert-btn");
  const statusEl = el("status");
  const errorBox = el("error-box");
  const warningsPanel = el("warnings-panel");
  const warningsList = el("warnings-list");
  const downloadsPanel = el("downloads-panel");
  const downloads = el("downloads");
  const pdfPanel = el("pdf-panel");
  const pdfTabs = el("pdf-tabs");
  const pdfEmbed = el("pdf-embed");
  const reportPanel = el("report-panel");
  const reportText = el("report-text");

  // Each entry: {file, role, number}. `number` only meaningful for figures.
  let entries = [];
  let journals = [];
  // Token for the most recent successful preview; the Export step copies THAT
  // result's artifacts. Cleared whenever inputs change so a stale preview can
  // never be exported (you must re-preview first).
  let lastExportToken = null;

  const ext = (name) => (name.split(".").pop() || "").toLowerCase();
  const setStatus = (t) => { statusEl.textContent = t || ""; };
  const showError = (m) => { errorBox.textContent = m; errorBox.classList.remove("hidden"); };
  const clearError = () => { errorBox.classList.add("hidden"); errorBox.textContent = ""; };

  function detectRole(file) {
    const e = ext(file.name);
    if (e === "docx") {
      const hasMain = entries.some((x) => x.role === "main");
      const looksSupp = /supp|_si|\bsi\b|supporting|supplement/i.test(file.name);
      return hasMain || looksSupp ? "supplement" : "main";
    }
    if (REF_EXTS.includes(e)) return "references";
    if (IMAGE_EXTS.includes(e)) return "figure";
    return "ignore";
  }

  function nextFigureNumber() {
    const used = entries.filter((x) => x.role === "figure").map((x) => x.number);
    let n = 1;
    while (used.includes(n)) n += 1;
    return n;
  }

  function addFiles(fileList) {
    Array.from(fileList).forEach((file) => {
      const role = detectRole(file);
      const number = role === "figure" ? nextFigureNumber() : null;
      entries.push({ file, role, number });
    });
    renderFiles();
    updateButton();
    invalidatePreview();
  }

  function renderFiles() {
    filelistBody.innerHTML = "";
    entries.forEach((entry, index) => {
      const tr = document.createElement("tr");

      const nameTd = document.createElement("td");
      nameTd.className = "name";
      nameTd.textContent = entry.file.name;
      tr.appendChild(nameTd);

      const roleTd = document.createElement("td");
      const roleSel = document.createElement("select");
      roleSel.className = "role";
      ROLES.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r;
        opt.textContent = ROLE_LABELS[r];
        if (r === entry.role) opt.selected = true;
        roleSel.appendChild(opt);
      });
      roleSel.addEventListener("change", () => {
        entry.role = roleSel.value;
        if (entry.role === "figure" && entry.number == null) entry.number = nextFigureNumber();
        renderFiles();
        updateButton();
        invalidatePreview();
      });
      roleTd.appendChild(roleSel);
      tr.appendChild(roleTd);

      const numTd = document.createElement("td");
      const numInput = document.createElement("input");
      numInput.type = "number";
      numInput.min = "1";
      numInput.className = "fignum";
      numInput.value = entry.number != null ? entry.number : "";
      numInput.hidden = entry.role !== "figure";
      numInput.addEventListener("change", () => {
        const v = parseInt(numInput.value, 10);
        entry.number = Number.isFinite(v) && v > 0 ? v : null;
        invalidatePreview();
      });
      numTd.appendChild(numInput);
      tr.appendChild(numTd);

      const rmTd = document.createElement("td");
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "remove-btn";
      rm.textContent = "✕";
      rm.setAttribute("aria-label", "Remove " + entry.file.name);
      rm.addEventListener("click", () => {
        entries.splice(index, 1);
        renderFiles();
        updateButton();
        invalidatePreview();
      });
      rmTd.appendChild(rm);
      tr.appendChild(rmTd);

      filelistBody.appendChild(tr);
    });
    filelist.classList.toggle("hidden", entries.length === 0);
  }

  function updateButton() {
    const hasMain = entries.filter((x) => x.role === "main").length === 1;
    convertBtn.disabled = !(hasMain && journalSelect.value);
  }

  // Export is only allowed after a successful preview, with a folder chosen and
  // at least one artifact ticked.
  function updateExportButton() {
    const dest = exportDir.value.trim();
    const anyType = Object.keys(EXPORT_TYPES).some((id) => el(id).checked);
    exportBtn.disabled = !(lastExportToken && dest && anyType);
    exportBtn.title = lastExportToken ? "" : "Preview a conversion first";
  }

  // Any change to the inputs makes the last preview stale, so its artifacts must
  // not be exported until the user previews again.
  function invalidatePreview() {
    lastExportToken = null;
    updateExportButton();
    // The review panel acts on the previewed session's token; once that is void
    // (inputs changed), review.js hides the now-stale corrections UI.
    window.LTXReview.reset();
  }

  // -- dropzone + input wiring --
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach((n) =>
    dropzone.addEventListener(n, (evt) => { evt.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((n) =>
    dropzone.addEventListener(n, (evt) => { evt.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (evt) => {
    if (evt.dataTransfer && evt.dataTransfer.files) addFiles(evt.dataTransfer.files);
  });

  // -- journals --
  function populateCitationModes() {
    const journal = journals.find((j) => j.name === journalSelect.value);
    citationSelect.innerHTML = "";
    if (!journal) return;
    journal.modes.forEach((mode) => {
      const opt = document.createElement("option");
      opt.value = mode;
      opt.textContent = CITATION_MODE_LABELS[mode] || mode;
      citationSelect.appendChild(opt);
    });
    citationSelect.disabled = journal.modes.length === 1;
    citationSelect.title = journal.modes.length === 1 ? "This journal's class supports only this citation style." : "";
  }

  async function loadJournals() {
    try {
      const resp = await fetch("/api/journals");
      if (!resp.ok) throw new Error("failed to load journals (" + resp.status + ")");
      journals = await resp.json();
      journalSelect.innerHTML = "";
      journals.forEach((journal) => {
        const opt = document.createElement("option");
        opt.value = journal.name;
        opt.textContent = journal.display_name || journal.name;
        journalSelect.appendChild(opt);
      });
      populateCitationModes();
      updateButton();
    } catch (err) {
      showError("Could not load journal list: " + err.message);
    }
  }
  journalSelect.addEventListener("change", () => {
    populateCitationModes(); updateButton(); invalidatePreview();
  });
  // Changing any option makes an earlier preview stale.
  [citationSelect, optPdf, optCombine, optSi1col, optZip, optAudit, optCheckRefs].forEach(
    (ctrl) => ctrl.addEventListener("change", invalidatePreview)
  );
  crossrefEmail.addEventListener("input", invalidatePreview);

  // -- export folder browse --
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
      showError("Folder picker unavailable — type a path instead: " + err.message);
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

  // -- export the previewed result to a folder --
  async function runExport() {
    if (!lastExportToken) {
      showError("Preview a conversion first, then export.");
      return;
    }
    const dest = exportDir.value.trim();
    const types = Object.entries(EXPORT_TYPES)
      .filter(([id]) => el(id).checked)
      .map(([, value]) => value);
    if (!dest || !types.length) {
      showError("Pick a destination folder and at least one file type to export.");
      return;
    }
    clearError();
    exportBtn.disabled = true;
    exportStatus.textContent = "Exporting…";
    try {
      const resp = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ export_token: lastExportToken, export_dir: dest, export_types: types }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "export failed (" + resp.status + ")");
      const what = body.exported && body.exported.length ? body.exported.join(", ") : "nothing";
      exportStatus.textContent = "Exported " + what + " to " + body.exported_to;
      if (body.warnings && body.warnings.length) {
        body.warnings.forEach((message) => {
          const li = document.createElement("li");
          li.textContent = message;
          warningsList.appendChild(li);
        });
        warningsPanel.classList.remove("hidden");
      }
    } catch (err) {
      exportStatus.textContent = "";
      showError(err.message);
    } finally {
      updateExportButton();
    }
  }
  exportBtn.addEventListener("click", runExport);

  // -- result rendering --
  function resetResultPanels() {
    warningsPanel.classList.add("hidden"); warningsList.innerHTML = "";
    downloadsPanel.classList.add("hidden"); downloads.innerHTML = "";
    reportPanel.classList.add("hidden"); reportText.textContent = "";
    pdfPanel.classList.add("hidden"); pdfTabs.innerHTML = ""; pdfEmbed.removeAttribute("src");
    window.LTXReview.reset();
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

  function buildFormData() {
    const fd = new FormData();
    const figures = entries.filter((x) => x.role === "figure");
    const main = entries.find((x) => x.role === "main");
    const supplement = entries.find((x) => x.role === "supplement");
    const references = entries.find((x) => x.role === "references");

    fd.append("main", main.file);
    fd.append("journal", journalSelect.value);
    if (citationSelect.value) fd.append("citation_style", citationSelect.value);
    if (crossrefEmail.value.trim()) fd.append("crossref_mailto", crossrefEmail.value.trim());
    if (supplement) fd.append("supplement", supplement.file);
    if (references) fd.append("references", references.file);
    figures.forEach((f) => {
      fd.append("figures", f.file);
      fd.append("figure_numbers", String(f.number || 1));
    });
    fd.append("pdf", optPdf.checked ? "true" : "false");
    fd.append("combine", optCombine.checked ? "true" : "false");
    fd.append("supplement_onecolumn", optSi1col.checked ? "true" : "false");
    fd.append("want_zip", optZip.checked ? "true" : "false");
    fd.append("exclude_figures", optNoFigs.checked ? "true" : "false");
    fd.append("equation_audit", optAudit.checked ? "true" : "false");
    fd.append("check_references", optCheckRefs.checked ? "true" : "false");
    // Preview never exports — the Export button copies the previewed result via
    // its export_token (see /api/export).
    return fd;
  }

  async function runConvert() {
    clearError();
    resetResultPanels();

    if (entries.filter((x) => x.role === "main").length !== 1) {
      showError("Assign exactly one file the “Main text” role.");
      return;
    }
    if (optCombine.checked && !entries.some((x) => x.role === "supplement")) {
      showError("“Combine supplement” needs a file with the Supplement role.");
      return;
    }
    if (optCombine.checked && !optPdf.checked) {
      showError("“Combine supplement” needs “Compile PDF” enabled.");
      return;
    }
    const figs = entries.filter((x) => x.role === "figure");
    if (figs.some((f) => !(f.number > 0))) {
      showError("Every figure needs a positive figure number.");
      return;
    }

    convertBtn.disabled = true;
    invalidatePreview();
    setStatus(optPdf.checked ? "Converting and compiling…" : "Converting…");
    try {
      const resp = await fetch("/api/convert-multi", { method: "POST", body: buildFormData() });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "conversion failed (" + resp.status + ")");

      lastExportToken = body.export_token || null;
      let statusMsg = (body.success ? "Preview ready. " : "Finished with errors — check the report below. ")
        + "Compiled from: " + body.output_dir;
      if (body.success) statusMsg += " · Choose a folder below and click Export to save.";
      setStatus(statusMsg);
      updateExportButton();

      if (body.warnings && body.warnings.length) {
        body.warnings.forEach((message) => {
          const li = document.createElement("li");
          li.textContent = message;
          warningsList.appendChild(li);
        });
        warningsPanel.classList.remove("hidden");
      }
      renderPdfTabs(body);
      renderDownloads(body);
      window.LTXReview.render(body.validation);
      if (body.report_md) {
        reportText.textContent = body.report_md;
        reportPanel.classList.remove("hidden");
      }
    } catch (err) {
      showError(err.message);
      setStatus("");
    } finally {
      updateButton();
    }
  }

  convertBtn.addEventListener("click", runConvert);

  // Bridge for review.js: read the live preview token and reuse the PDF-tab
  // renderer after an apply-corrections recompile.
  window.LTXApp = { exportToken: () => lastExportToken, renderPdfTabs: renderPdfTabs };

  loadJournals();
})();
