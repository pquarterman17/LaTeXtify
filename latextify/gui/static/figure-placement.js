/* Per-figure width picker for the main conversion form.

   The controller inventories the selected manuscript through /api/figures,
   merges in separately uploaded replacements by number, and serializes only
   manual choices. The conversion pipeline remains the source of truth for
   Automatic placement. */
(function () {
  "use strict";

  const el = (id) => document.getElementById(id);

  function create(config) {
    const panel = el("figure-placement-panel");
    const status = el("figure-placement-status");
    const tableBody = el("figure-placement-body");
    let inventoriedMain = null;
    let inventory = [];
    let requestNumber = 0;
    const choices = new Map();

    function combinedFigures() {
      const uploaded = new Map(
        config.getUploadedFigures().filter((fig) => fig.number > 0)
          .map((fig) => [fig.number, fig.name])
      );
      const figures = new Map(inventory.map((fig) => [fig.number, fig]));
      uploaded.forEach((name, number) => {
        if (!figures.has(number)) figures.set(number, { number, caption: "", source: name });
      });
      return { uploaded, ordered: Array.from(figures.values()).sort((a, b) => a.number - b.number) };
    }

    function render() {
      const main = config.getMainFile();
      const { uploaded, ordered } = combinedFigures();
      tableBody.replaceChildren();
      ordered.forEach((fig) => {
        const row = document.createElement("tr");
        const number = document.createElement("td");
        number.textContent = String(fig.number);
        const description = document.createElement("td");
        const replacement = uploaded.get(fig.number);
        description.textContent = replacement || fig.caption || fig.source || "Embedded figure";
        if (replacement && fig.caption) description.title = fig.caption;
        const width = document.createElement("td");
        const select = document.createElement("select");
        [["auto", "Automatic"], ["one", "1 column"], ["two", "2 columns"]]
          .forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value; option.textContent = label;
            if ((choices.get(fig.number) || "auto") === value) option.selected = true;
            select.appendChild(option);
          });
        select.setAttribute("aria-label", "Width for figure " + fig.number);
        select.addEventListener("change", () => {
          if (select.value === "auto") choices.delete(fig.number);
          else choices.set(fig.number, select.value);
          config.invalidate();
        });
        width.appendChild(select);
        row.append(number, description, width);
        tableBody.appendChild(row);
      });
      panel.classList.toggle("hidden", !main);
      if (ordered.length && !status.textContent.startsWith("Reading")) {
        status.textContent = ordered.length + " figure" +
          (ordered.length === 1 ? "" : "s") + " found. Choose each width below.";
      } else if (!ordered.length && main && !status.textContent.startsWith("Reading")) {
        status.textContent = "No figures found in the selected manuscript.";
      }
    }

    async function sync() {
      const main = config.getMainFile();
      if (!main) {
        requestNumber += 1;
        inventoriedMain = null; inventory = []; choices.clear();
        status.textContent = ""; render();
        return;
      }
      if (inventoriedMain === main) { render(); return; }
      inventoriedMain = main;
      inventory = [];
      choices.clear();
      const request = ++requestNumber;
      status.textContent = "Reading figures from " + main.name + "…";
      render();
      const form = new FormData();
      form.append("main", main);
      try {
        const response = await fetch("/api/figures", { method: "POST", body: form });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "could not inspect figures");
        if (request !== requestNumber || inventoriedMain !== main) return;
        inventory = body.figures || [];
        status.textContent = "";
        render();
      } catch (err) {
        if (request !== requestNumber) return;
        status.textContent = "Could not list embedded figures automatically: " + err.message +
          ". Uploaded figure numbers can still be set below.";
        render();
      }
    }

    function serialize() {
      return Array.from(choices.entries()).sort((a, b) => a[0] - b[0])
        .map(([number, mode]) => number + "=" + mode).join(",");
    }

    return { sync, serialize };
  }

  window.LTXFigurePlacement = { create };
})();
