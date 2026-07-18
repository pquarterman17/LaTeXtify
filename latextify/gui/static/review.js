/* LaTeXtify GUI — reference-review panel (split from index.html; served at
   /static/review.js). Talks to the main app through window.LTXApp (preview
   token, PDF-tab renderer) and exposes window.LTXReview so app.js can render
   and reset this panel. Buildless vanilla JS. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);
  const reviewPanel = el("review-panel");
  const reviewSummary = el("review-summary");
  const reviewCards = el("review-cards");
  const reviewStatus = el("review-status");
  const applyBtn = el("apply-btn");

  // key -> {action:"approve"|"edit", entry?} for references the author chose to
  // change. "Keep mine" (deny) records nothing, so Apply with no interaction is
  // a safe no-op. Rebuilt on every preview.
  let reviewDecisions = {};

  // Editable reference fields shown in the whole-entry editor (server contract:
  // these keys round-trip through entry_to_dict / entry_from_dict).
  const REVIEW_FIELDS = [
    ["title", "Title", true], ["authors", "Authors (Family, Given; …)", true], ["year", "Year", false], ["journal", "Journal", true],
    ["volume", "Volume", false], ["issue", "Issue", false], ["pages", "Pages", false], ["doi", "DOI", true],
  ];
  const REVIEW_STATUS_LABELS = {
    mismatch: "field mismatch", dead_doi: "dead DOI",
    doi_suggested: "missing DOI", unverifiable: "unverifiable",
  };

  function makeField(field, label, wide, value) {
    const wrap = document.createElement("div");
    wrap.className = "field" + (wide ? " wide" : "");
    const lab = document.createElement("label");
    lab.textContent = label;
    const input = document.createElement("input");
    input.type = "text"; input.value = value || ""; input.dataset.field = field;
    wrap.appendChild(lab); wrap.appendChild(input);
    return { wrap, input };
  }

  function buildCard(rec) {
    const card = document.createElement("div");
    card.className = "review-card";

    const head = document.createElement("div");
    head.className = "review-head";
    const key = document.createElement("code"); key.textContent = rec.key;
    const tag = document.createElement("span");
    tag.className = "review-tag";
    tag.textContent = REVIEW_STATUS_LABELS[rec.status] || rec.status;
    head.appendChild(key); head.appendChild(tag);
    card.appendChild(head);

    (rec.problems || []).forEach((p) => {
      const div = document.createElement("div");
      div.className = "review-diffs";
      const li = document.createElement("li");
      li.style.listStyle = "none";
      li.textContent = p.field + ": yours “" + p.ours + "” → Crossref “" + p.canonical + "”";
      div.appendChild(li); card.appendChild(div);
    });
    if (rec.status === "dead_doi" && rec.doi) {
      const n = document.createElement("p"); n.className = "review-note";
      n.textContent = "Current DOI does not resolve: " + rec.doi;
      card.appendChild(n);
    }
    if (rec.status === "doi_suggested" && rec.suggested_doi) {
      const n = document.createElement("p"); n.className = "review-note";
      n.textContent = "Suggested DOI: " + rec.suggested_doi;
      card.appendChild(n);
    }

    // Whole-entry editor (hidden until "Edit" is chosen), prefilled with our entry.
    const form = document.createElement("div");
    form.className = "edit-form hidden";
    const inputs = {};
    REVIEW_FIELDS.forEach(([field, label, wide]) => {
      const f = makeField(field, label, wide, (rec.entry || {})[field]);
      inputs[field] = f.input; form.appendChild(f.wrap);
    });
    function readForm() {
      const entry = {};
      Object.keys(inputs).forEach((k) => { entry[k] = inputs[k].value; });
      return entry;
    }
    Object.values(inputs).forEach((inp) =>
      inp.addEventListener("input", () => {
        if (reviewDecisions[rec.key] && reviewDecisions[rec.key].action === "edit") {
          reviewDecisions[rec.key].entry = readForm();
        }
      })
    );

    // Decision buttons. Default = "Keep mine" (no decision recorded).
    const actions = document.createElement("div");
    actions.className = "review-actions";
    const hasFix = !!rec.canonical || !!rec.suggested_doi;
    const approveBtn = document.createElement("button");
    approveBtn.type = "button"; approveBtn.textContent = "Approve fix";
    approveBtn.disabled = !hasFix;
    const keepBtn = document.createElement("button");
    keepBtn.type = "button"; keepBtn.textContent = "Keep mine";
    const editBtn = document.createElement("button");
    editBtn.type = "button"; editBtn.textContent = "Edit…";
    const all = [approveBtn, keepBtn, editBtn];
    function choose(btn) { all.forEach((b) => b.classList.toggle("chosen", b === btn)); }
    approveBtn.addEventListener("click", () => {
      reviewDecisions[rec.key] = { action: "approve" };
      form.classList.add("hidden"); choose(approveBtn);
    });
    keepBtn.addEventListener("click", () => {
      delete reviewDecisions[rec.key];
      form.classList.add("hidden"); choose(keepBtn);
    });
    editBtn.addEventListener("click", () => {
      reviewDecisions[rec.key] = { action: "edit", entry: readForm() };
      form.classList.remove("hidden"); choose(editBtn);
    });
    actions.appendChild(approveBtn); actions.appendChild(keepBtn); actions.appendChild(editBtn);
    choose(keepBtn);
    card.appendChild(actions);
    card.appendChild(form);
    return card;
  }

  function renderReview(validation) {
    reviewDecisions = {};
    reviewCards.innerHTML = ""; reviewSummary.textContent = ""; reviewStatus.textContent = "";
    if (!validation) { reviewPanel.classList.add("hidden"); return; }
    const counts = validation.counts || {};
    const parts = Object.keys(counts).map((k) => counts[k] + " " + k.replace(/_/g, " "));
    reviewSummary.textContent =
      "Checked " + validation.total + " reference(s): " + parts.join(", ") + ".";
    const records = validation.records || [];
    if (!records.length) {
      const p = document.createElement("p");
      p.className = "review-clean";
      p.textContent = "All references verified cleanly. ✓";
      reviewCards.appendChild(p);
      applyBtn.classList.add("hidden");
    } else {
      applyBtn.classList.remove("hidden"); applyBtn.disabled = false;
      records.forEach((rec) => reviewCards.appendChild(buildCard(rec)));
    }
    reviewPanel.classList.remove("hidden");
  }

  async function runApply() {
    const token = window.LTXApp.exportToken();
    if (!token) {
      reviewStatus.textContent = "Preview again before applying (the session expired).";
      return;
    }
    const decisions = Object.keys(reviewDecisions).map((key) =>
      Object.assign({ key: key }, reviewDecisions[key]));
    if (!decisions.length) {
      reviewStatus.textContent = "Nothing to apply — choose “Approve fix” or “Edit…” on at least one reference.";
      return;
    }
    applyBtn.disabled = true;
    reviewStatus.textContent = "Applying corrections and recompiling…";
    try {
      const resp = await fetch("/api/apply-corrections", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ export_token: token, decisions: decisions }),
      });
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.detail || "apply failed (" + resp.status + ")");
      let msg = "Applied " + body.applied + " correction(s). references.bib updated";
      msg += body.pdf_url ? " and recompiled." : ".";
      if (body.pdf_url && !body.success) msg += " (compile reported errors — see report.md)";
      reviewStatus.textContent = msg;
      if (body.pdf_url || body.supplement_pdf_url || body.combined_pdf_url) {
        window.LTXApp.renderPdfTabs(body);
      }
    } catch (err) {
      reviewStatus.textContent = "Error: " + err.message;
      applyBtn.disabled = false;
    }
  }
  applyBtn.addEventListener("click", runApply);

  // The review panel acts on a previewed session; when the preview goes stale
  // or a new one starts, app.js calls reset() to clear and hide everything.
  function resetReview() {
    reviewPanel.classList.add("hidden");
    reviewCards.innerHTML = ""; reviewSummary.textContent = "";
    reviewStatus.textContent = ""; reviewDecisions = {};
  }

  window.LTXReview = { render: renderReview, reset: resetReview };
})();
