(function () {
  const STORAGE_KEY = "erp_relatorio_form_diagnostics";
  const MAX_ENTRIES = 80;

  function safeDetails(details) {
    try {
      return JSON.parse(JSON.stringify(details || {}));
    } catch (error) {
      return { serializacao: "falhou", texto: String(details) };
    }
  }

  function readEntries() {
    try {
      return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
    } catch (error) {
      return [];
    }
  }

  function writeEntries(entries) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)));
  }

  function renderPanel() {
    const panel = document.getElementById("relatorio-form-debug");
    const output = document.getElementById("relatorio-form-debug-output");
    if (!panel || !output) return;

    const entries = readEntries();
    if (!entries.length) return;

    panel.classList.remove("d-none");
    output.textContent = JSON.stringify(entries.slice(-20), null, 2);
  }

  function entry(level, event, details) {
    return {
      timestamp: new Date().toISOString(),
      level,
      event,
      url: window.location.href,
      userAgent: window.navigator.userAgent,
      details: safeDetails(details),
    };
  }

  function log(event, details, level = "info") {
    const entries = readEntries();
    const item = entry(level, event, details);
    entries.push(item);
    writeEntries(entries);

    const method = level === "error" ? "error" : level === "warning" ? "warn" : "info";
    if (window.console && typeof window.console[method] === "function") {
      window.console[method]("[RelatorioForm]", event, details || {});
    }

    if (level === "error" || level === "warning") {
      renderPanel();
    }
    return item;
  }

  function error(event, err, details) {
    const payload = {
      message: err && err.message ? err.message : String(err),
      stack: err && err.stack ? err.stack : "",
      details: details || {},
    };
    return log(event, payload, "error");
  }

  function clear() {
    window.localStorage.removeItem(STORAGE_KEY);
    const panel = document.getElementById("relatorio-form-debug");
    const output = document.getElementById("relatorio-form-debug-output");
    if (panel) panel.classList.add("d-none");
    if (output) output.textContent = "";
  }

  function download() {
    const entries = readEntries();
    const blob = new Blob([JSON.stringify(entries, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const link = document.createElement("a");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = URL.createObjectURL(blob);
    link.download = `relatorio-form-log-${timestamp}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  function bindPanel() {
    document.getElementById("btn-baixar-log-form")?.addEventListener("click", download);
    document.getElementById("btn-copiar-log-form")?.addEventListener("click", async () => {
      const texto = JSON.stringify(readEntries(), null, 2);
      try {
        await window.navigator.clipboard.writeText(texto);
        log("log_copiado", { total: readEntries().length });
      } catch (err) {
        error("falha_copiar_log", err);
      }
    });
    renderPanel();
  }

  window.RelatorioFormDiagnostics = {
    log,
    error,
    clear,
    download,
    entries: readEntries,
    show: renderPanel,
  };

  window.addEventListener("error", (event) => {
    error("javascript_error", event.error || event.message, {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    error("javascript_unhandled_rejection", event.reason || "Promise rejeitada");
  });

  document.addEventListener("DOMContentLoaded", bindPanel);
})();
