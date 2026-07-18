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
  const REF_EXTS = ["bib", "ris", "json", "xml", "nbib"];
  const CITATION_MODE_LABELS = { numeric: "numeric — [1], [2]", authoryear: "author–year — (Doe, 2020)" };
  const el = (id) => document.getElementById(id);
  const dropzone = el("dropzone");
  const fileInput = el("file-input");
  const filelist = el("filelist");
  const filelistBody = el("filelist-body");
  const journalSelect = el("journal-select");
  const citationSelect = el("citation-select");
  const crossrefEmail = el("crossref-email");
  const optPdf = el("opt-pdf"), optCombine = el("opt-combine"), optZip = el("opt-zip"),
    optNoFigs = el("opt-nofigs"), optAudit = el("opt-audit"), optCheckRefs = el("opt-checkrefs"),
    optAnon = el("opt-anonymize"), optFigsEnd = el("opt-figsend");
  const convertBtn = el("convert-btn");
  const statusEl = el("status");
  const errorBox = el("error-box");

  // Advertise the real accepted formats from the same lists role detection
  // uses, and keep the file picker's filter in sync (one source of truth —
  // the server's upload allowlists mirror these).
  el("dropzone-text").innerHTML =
    "Drag &amp; drop files here — manuscript <strong>.docx</strong>, figures (<strong>." +
    IMAGE_EXTS.join(" .") + "</strong>), references (<strong>." + REF_EXTS.join(" .") +
    "</strong>) — or click to choose";
  fileInput.setAttribute(
    "accept",
    [".docx"].concat(IMAGE_EXTS.map((e) => "." + e), REF_EXTS.map((e) => "." + e)).join(",")
  );

  // Each entry: {file, role, number}. `number` only meaningful for figures.
  let entries = [];
  let journals = [];
  // Token for the most recent successful preview; the Export step copies THAT
  // result's artifacts. Cleared whenever inputs change so a stale preview can
  // never be exported (you must re-preview first).
  let lastExportToken = null;
  // Citation-style override state (plan item 4): a pick that differs from the
  // journal's declared default blocks conversion until confirmed or reverted;
  // a confirmed override holds until the journal changes.
  let citationOverridePending = false;
  let citationConfirmedJournal = null;

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

  const defaultLayout = () => ({ columns: "default", linenos: false, dblspace: false });

  function addFiles(fileList) {
    Array.from(fileList).forEach((file) => {
      const role = detectRole(file);
      const number = role === "figure" ? nextFigureNumber() : null;
      entries.push({ file, role, number, layout: defaultLayout() });
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
        // The supplement's column choices don't include "two" (its one-column
        // choice IS the plain-article format); drop a stale main-only pick.
        if (entry.role === "supplement" && entry.layout && entry.layout.columns === "two") {
          entry.layout.columns = "default";
        }
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
      if (entry.role === "main" || entry.role === "supplement") {
        if (!entry.layout) entry.layout = defaultLayout();
        filelistBody.appendChild(buildLayoutRow(entry));
      }
    });
    filelist.classList.toggle("hidden", entries.length === 0);
  }

  // Per-document layout mini-panel (plan item 6): a second table row under a
  // Main/Supplement file carrying its column mode, line numbers, and spacing.
  const COLUMN_CHOICES = {
    main: [["default", "journal default"], ["one", "one-column"], ["two", "two-column"]],
    supplement: [["default", "journal default"], ["one", "one-column (article)"]],
  };

  function buildLayoutRow(entry) {
    const tr = document.createElement("tr");
    tr.className = "layout-row";
    const td = document.createElement("td");
    td.colSpan = 4;
    const wrap = document.createElement("div");
    wrap.className = "doc-layout";
    const label = document.createElement("span");
    label.textContent = "Layout:";
    wrap.appendChild(label);

    const sel = document.createElement("select");
    sel.title = "Column mode for this document. On APS/AIP journals one-column is REVTeX's "
      + "preprint mode and two-column its reprint mode; the supplement's one-column choice "
      + "is the simplified article format.";
    COLUMN_CHOICES[entry.role].forEach(([value, text]) => {
      const opt = document.createElement("option");
      opt.value = value; opt.textContent = text;
      if (value === entry.layout.columns) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", () => { entry.layout.columns = sel.value; invalidatePreview(); });
    wrap.appendChild(sel);

    const mk = (key, text, title) => {
      const lab = document.createElement("label");
      lab.className = "checkbox-row";
      lab.title = title;
      const box = document.createElement("input");
      box.type = "checkbox"; box.checked = entry.layout[key];
      box.addEventListener("change", () => { entry.layout[key] = box.checked; invalidatePreview(); });
      lab.appendChild(box); lab.appendChild(document.createTextNode(" " + text));
      return lab;
    };
    wrap.appendChild(mk("linenos", "Line numbers",
      "Reviewer line numbers (REVTeX's native class option where available; the lineno package elsewhere)."));
    wrap.appendChild(mk("dblspace", "Double spacing",
      "Double-space this document via the setspace package."));

    td.appendChild(wrap);
    tr.appendChild(td);
    return tr;
  }

  function updateButton() {
    const hasMain = entries.filter((x) => x.role === "main").length === 1;
    convertBtn.disabled = !(hasMain && journalSelect.value) || citationOverridePending;
    convertBtn.title = citationOverridePending
      ? "Confirm or revert the citation-style choice first." : "";
    updateOptionState();
  }

  // Input-aware options: supplement-dependent toggles only mean something
  // once a Supplement file exists, and exclude-figures deserves a warning
  // when it will silently ignore staged figure files.
  function updateOptionState() {
    const hasSupp = entries.some((x) => x.role === "supplement");
    optCombine.disabled = !hasSupp;
    if (!hasSupp) optCombine.checked = false;
    optCombine.title = hasSupp ? "" : "Add a file with the Supplement role to enable.";
    const figsStaged = entries.some((x) => x.role === "figure");
    el("nofigs-warning").classList.toggle("hidden", !(optNoFigs.checked && figsStaged));
  }

  // Any change to the inputs makes the last preview stale, so its artifacts must
  // not be exported until the user previews again.
  function invalidatePreview() {
    lastExportToken = null;
    window.LTXExport.update();
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

  // -- journals + citation-style default tracking --
  const modeLabel = (mode) => CITATION_MODE_LABELS[mode] || mode;
  const currentJournal = () => journals.find((j) => j.name === journalSelect.value);
  const citationConfirmRow = el("citation-confirm");

  function hideCitationConfirm() {
    citationOverridePending = false;
    citationConfirmRow.classList.add("hidden");
  }

  function populateCitationModes() {
    const journal = currentJournal();
    citationSelect.innerHTML = "";
    citationConfirmedJournal = null;
    hideCitationConfirm();
    if (!journal) return;
    journal.modes.forEach((mode) => {
      const opt = document.createElement("option");
      opt.value = mode;
      opt.textContent = modeLabel(mode);
      citationSelect.appendChild(opt);
    });
    // Follow the journal's house style; a different pick must be confirmed.
    if (journal.modes.includes(journal.default_mode)) {
      citationSelect.value = journal.default_mode;
    }
    citationSelect.disabled = journal.modes.length === 1;
    citationSelect.title = journal.modes.length === 1 ? "This journal's class supports only this citation style." : "";
  }

  function onCitationChange() {
    const journal = currentJournal();
    if (!journal) return;
    if (citationSelect.value === journal.default_mode || citationConfirmedJournal === journal.name) {
      hideCitationConfirm();
    } else {
      el("citation-confirm-text").textContent =
        (journal.display_name || journal.name) + "'s standard is " + modeLabel(journal.default_mode) +
        " — use " + modeLabel(citationSelect.value) + " anyway?";
      citationOverridePending = true;
      citationConfirmRow.classList.remove("hidden");
    }
    updateButton();
  }
  el("citation-confirm-yes").addEventListener("click", () => {
    const journal = currentJournal();
    if (journal) citationConfirmedJournal = journal.name;
    hideCitationConfirm(); updateButton();
  });
  el("citation-confirm-no").addEventListener("click", () => {
    const journal = currentJournal();
    if (journal) citationSelect.value = journal.default_mode;
    hideCitationConfirm(); updateButton();
  });

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
  // Changing any option makes an earlier preview stale (opt-nofigs included:
  // it changes what the conversion emits, so a prior preview no longer applies).
  [
    citationSelect, optPdf, optCombine, optZip, optNoFigs,
    optAudit, optCheckRefs, optAnon, optFigsEnd,
  ].forEach((ctrl) => ctrl.addEventListener("change", invalidatePreview));
  optNoFigs.addEventListener("change", updateOptionState);
  citationSelect.addEventListener("change", onCitationChange);
  crossrefEmail.addEventListener("input", invalidatePreview);

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
    const mainLayout = main.layout || defaultLayout();
    fd.append("main_columns", mainLayout.columns || "default");
    fd.append("main_line_numbers", mainLayout.linenos ? "true" : "false");
    fd.append("main_double_spacing", mainLayout.dblspace ? "true" : "false");
    if (supplement) {
      const suppLayout = supplement.layout || defaultLayout();
      fd.append("supplement_columns", suppLayout.columns || "default");
      fd.append("supplement_line_numbers", suppLayout.linenos ? "true" : "false");
      fd.append("supplement_double_spacing", suppLayout.dblspace ? "true" : "false");
    }
    fd.append("pdf", optPdf.checked ? "true" : "false");
    fd.append("combine", optCombine.checked ? "true" : "false");
    fd.append("want_zip", optZip.checked ? "true" : "false");
    fd.append("exclude_figures", optNoFigs.checked ? "true" : "false");
    fd.append("equation_audit", optAudit.checked ? "true" : "false");
    fd.append("check_references", optCheckRefs.checked ? "true" : "false");
    fd.append("anonymize", optAnon.checked ? "true" : "false");
    fd.append("figures_at_end", optFigsEnd.checked ? "true" : "false");
    // Preview never exports — the Export button copies the previewed result via
    // its export_token (see /api/export).
    return fd;
  }

  async function runConvert() {
    clearError();
    window.LTXResults.reset();
    window.LTXReview.reset();

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
      window.LTXExport.update();

      (body.warnings || []).forEach(window.LTXResults.appendWarning);
      window.LTXResults.renderPdfTabs(body);
      window.LTXResults.renderDownloads(body);
      window.LTXReview.render(body.validation);
      window.LTXResults.showReport(body.report_md);
    } catch (err) {
      showError(err.message);
      setStatus("");
    } finally {
      updateButton();
    }
  }

  convertBtn.addEventListener("click", runConvert);

  // Bridge for review.js + export.js: the live preview token, the PDF-tab
  // renderer (reused after an apply-corrections recompile), and the shared
  // error/warning surfaces (forwarded to results.js).
  window.LTXApp = {
    exportToken: () => lastExportToken,
    renderPdfTabs: (body) => window.LTXResults.renderPdfTabs(body),
    showError: showError,
    clearError: clearError,
    appendWarning: (message) => window.LTXResults.appendWarning(message),
  };

  loadJournals();
})();
