/* Local runtime diagnostics and content-free support-report download. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  const button = el("system-check-btn");
  const save = el("system-check-save");
  const status = el("system-check-status");
  const report = el("system-check-report");
  let reportText = "";

  button.addEventListener("click", async () => {
    button.disabled = true;
    save.disabled = true;
    status.textContent = "Checking the local installation…";
    report.classList.add("hidden");
    try {
      const response = await fetch("/api/system-check", { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "System check failed");
      reportText = body.report || "";
      report.textContent = reportText;
      report.classList.remove("hidden");
      status.textContent = body.overall === "pass"
        ? "Everything required is working."
        : body.overall === "warn"
          ? "The installation works, with the warnings shown below."
          : "One or more required checks failed.";
      save.disabled = !reportText;
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      button.disabled = false;
    }
  });

  save.addEventListener("click", () => {
    if (!reportText) return;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([reportText], { type: "text/plain" }));
    link.download = "latextify-system-check.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
  });
})();
