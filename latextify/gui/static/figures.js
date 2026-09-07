/* LaTeXtify GUI — "What are my figures?" panel (served at /static/figures.js).

   Independent of the main conversion flow, one action on one upload:

     List figures -> POST /api/figures   (writes nothing, no download)

   The panel exists because figure overrides are keyed by NUMBER, and the
   number is the image's position in the document. An author who assumes it
   matches the caption ("Figure 3") can silently swap two figures in a
   submission when a caption has no image behind it and shifts everything
   after it. Showing number, caption and current format together turns that
   guess into a lookup.

   KIND is rendered from the server's classification rather than the file
   extension, so a screenshot printed to PDF is called what it is instead of
   passing as vector art.

   Uses window.LTXApp for the shared error helpers set up by app.js.
   Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const fileInput = el("figures-file");
  const listBtn = el("figures-btn");
  const statusEl = el("figures-status");
  const reportEl = el("figures-report");

  const KIND_LABEL = {
    vector: "vector",
    raster: "raster",
    "raster-in-pdf": "raster in a PDF",
    unknown: "unreadable",
  };

  function reset() {
    statusEl.textContent = "";
    reportEl.classList.add("hidden");
    reportEl.replaceChildren();
  }

  fileInput.addEventListener("change", () => {
    listBtn.disabled = fileInput.files.length === 0;
    reset();
  });

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  /* Pixels for a raster, points for a measured vector page, "—" when the
     file could not be measured at all. */
  function sizeText(fig) {
    if (fig.width == null || fig.height == null) return "—";
    const unit = fig.kind === "raster" ? "" : " pt";
    return Math.round(fig.width) + " × " + Math.round(fig.height) + unit;
  }

  /* Vector art has no resolution to report — that is the point of supplying
     it — so it gets a dash rather than a number. */
  function dpiText(fig, minDpi) {
    if (fig.dpi == null) return fig.is_vector ? "—" : "?";
    const rounded = Math.round(fig.dpi);
    return rounded + (rounded < minDpi ? " — too low" : "");
  }

  function renderTable(body) {
    const table = document.createElement("table");
    table.className = "filelist";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Fig #", "Source", "Kind", "Size", "DPI in print", "Caption"].forEach((label) => {
      const th = document.createElement("th");
      th.textContent = label;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    body.figures.forEach((fig) => {
      const tr = document.createElement("tr");
      tr.appendChild(cell(String(fig.number)));
      tr.appendChild(cell(fig.source));
      tr.appendChild(cell(KIND_LABEL[fig.kind] || fig.kind));
      tr.appendChild(cell(sizeText(fig)));
      tr.appendChild(cell(dpiText(fig, body.min_print_dpi)));
      const caption = fig.caption || (fig.in_table ? "(in a table cell)" : "(no caption found)");
      tr.appendChild(cell(caption, "name"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function renderSummary(body) {
    const notes = [];
    const attention = body.figures.filter((f) => f.needs_attention).map((f) => f.number);
    const vector = body.figures.filter((f) => f.is_vector).length;
    notes.push(vector + " of " + body.figures.length + " already vector.");
    if (attention.length) {
      notes.push(
        "Figure(s) " + attention.join(", ") + " would print below " + body.min_print_dpi +
        " DPI. Supply a vector version of each (PDF, EPS or SVG) as a Figure upload with " +
        "the matching number, or as figures/fig<N>.pdf beside the manuscript."
      );
    }
    const wrap = paragraphs(notes);
    /* A caption with no image behind it shifts every later figure's number,
       which is precisely how an override lands on the wrong figure. */
    const gaps = renderGaps(body);
    if (gaps) wrap.appendChild(gaps.firstChild);
    return wrap;
  }

  function paragraphs(notes) {
    const wrap = document.createElement("div");
    wrap.className = "hint";
    notes.forEach((text) => {
      const p = document.createElement("p");
      p.textContent = text;
      wrap.appendChild(p);
    });
    return wrap;
  }

  function renderGaps(body) {
    if (!body.caption_gaps.length) return null;
    return paragraphs([
      "Caption(s) with no image: " + body.caption_gaps.join(", ") +
      ". Every figure after a gap is numbered one lower than its caption says.",
    ]);
  }

  listBtn.addEventListener("click", async () => {
    if (!fileInput.files.length) return;
    window.LTXApp.clearError();
    reset();
    listBtn.disabled = true;
    statusEl.textContent = "Reading figures...";
    try {
      const fd = new FormData();
      fd.append("main", fileInput.files[0]);
      const resp = await fetch("/api/figures", { method: "POST", body: fd });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "could not read the figures");

      statusEl.textContent = "";
      if (body.figures.length) {
        reportEl.replaceChildren(renderTable(body), renderSummary(body));
      } else {
        /* No table, and no "0 of 0 already vector" — but the caption-gap note
           still matters here: captions with no image behind them is exactly
           what a figure-less document with figure captions looks like. */
        const p = document.createElement("p");
        p.className = "hint";
        p.textContent = "No figures found in this document.";
        reportEl.replaceChildren(p);
        const gaps = renderGaps(body);
        if (gaps) reportEl.appendChild(gaps);
      }
      reportEl.classList.remove("hidden");
    } catch (err) {
      statusEl.textContent = "";
      window.LTXApp.showError(err.message);
    } finally {
      listBtn.disabled = fileInput.files.length === 0;
    }
  });
})();
