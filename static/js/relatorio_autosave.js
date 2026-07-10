(function () {
  "use strict";

  const SERVER_DEBOUNCE_MS = 3000;
  const SERVER_MIN_INTERVAL_MS = 120000;
  const LOCAL_DEBOUNCE_MS = 250;

  function nowLabel(date = new Date()) {
    return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }

  function getCsrf(form) {
    return form.querySelector('[name="csrfmiddlewaretoken"]')?.value || "";
  }

  function setStatus(state, text) {
    const badge = document.getElementById("autosave-status");
    const label = document.getElementById("autosave-status-text");
    if (!badge || !label) return;
    badge.dataset.state = state;
    label.textContent = text;
  }

  function updateElementAttributesForSnapshot(root) {
    root.querySelectorAll("input, textarea, select").forEach((el) => {
      if (el.tagName === "TEXTAREA") {
        el.textContent = el.value || "";
        return;
      }
      if (el.tagName === "SELECT") {
        Array.from(el.options || []).forEach((option) => {
          if (option.selected) option.setAttribute("selected", "selected");
          else option.removeAttribute("selected");
        });
        return;
      }
      if (el.type === "checkbox" || el.type === "radio") {
        if (el.checked) el.setAttribute("checked", "checked");
        else el.removeAttribute("checked");
        return;
      }
      if (el.type !== "file") {
        el.setAttribute("value", el.value || "");
      }
    });
  }

  function htmlSnapshot(selector) {
    const node = document.querySelector(selector);
    if (!node) return "";
    const clone = node.cloneNode(true);
    updateElementAttributesForSnapshot(clone);
    return clone.innerHTML;
  }

  function collectEntries(form) {
    const data = new FormData(form);
    const entries = [];
    data.forEach((value, key) => {
      if (key === "csrfmiddlewaretoken") return;
      if (value instanceof File) {
        entries.push([key, value.name ? { fileName: value.name, size: value.size, type: value.type } : ""]);
      } else {
        entries.push([key, value]);
      }
    });
    return entries;
  }

  function applyEntries(form, entries) {
    const grouped = new Map();
    (entries || []).forEach(([key, value]) => {
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(value && typeof value === "object" ? "" : String(value ?? ""));
    });

    grouped.forEach((values, key) => {
      const fields = Array.from(form.querySelectorAll(`[name="${CSS.escape(key)}"]`));
      if (!fields.length) return;
      if (fields[0].type === "checkbox" || fields[0].type === "radio") {
        fields.forEach((field) => {
          field.checked = values.includes(field.value);
          field.dispatchEvent(new Event("change", { bubbles: true }));
        });
        return;
      }
      if (fields[0].tagName === "SELECT" && fields[0].multiple) {
        const selected = new Set(values);
        Array.from(fields[0].options).forEach((option) => {
          option.selected = selected.has(option.value);
        });
        fields[0].dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      fields[0].value = values[0] || "";
      fields[0].dispatchEvent(new Event("input", { bubbles: true }));
      fields[0].dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function countRows(selector) {
    return document.querySelectorAll(selector).length;
  }

  function callOptional(name) {
    if (typeof window[name] === "function") {
      try { window[name](); } catch (_error) { /* noop */ }
    }
  }

  function init() {
    const form = document.getElementById("form-relatorio");
    if (!form || form.dataset.autosaveEnabled !== "1") {
      setStatus("saved", "AutoSave indisponível");
      return;
    }

    const endpoint = form.dataset.autosaveUrl || "";
    const storageKey = form.dataset.autosaveStorageKey || "relatorio-autosave";
    const autosaveKeyInput = document.getElementById("id_autosave_key");
    const keyStorageName = `${storageKey}:key`;
    const existingKey = localStorage.getItem(keyStorageName);
    const generatedKey = existingKey || (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    localStorage.setItem(keyStorageName, generatedKey);
    if (autosaveKeyInput && !autosaveKeyInput.value) autosaveKeyInput.value = generatedKey;

    let dirty = false;
    let saving = false;
    let restoring = false;
    let submitting = false;
    let localTimer = null;
    let serverTimer = null;
    let lastServerSaveAt = 0;

    function snapshot() {
      return {
        version: 1,
        savedAt: new Date().toISOString(),
        url: window.location.href,
        relatorioId: form.dataset.relatorioId || "",
        entries: collectEntries(form),
        despesasHtml: htmlSnapshot("#corpo-despesas"),
        trechosHtml: htmlSnapshot("#corpo-trechos"),
        counts: {
          despesas: countRows(".linha-despesa"),
          trechos: countRows(".linha-trecho"),
        },
      };
    }

    function saveLocal() {
      try {
        localStorage.setItem(storageKey, JSON.stringify(snapshot()));
      } catch (_error) {
        // LocalStorage pode falhar por limite do navegador. O backend continua tentando.
      }
    }

    function restoreLocalIfWanted() {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return;
      let data = null;
      try {
        data = JSON.parse(raw);
      } catch (_error) {
        localStorage.removeItem(storageKey);
        return;
      }
      if (!data || !data.entries || !data.savedAt) return;
      const savedAt = new Date(data.savedAt);
      if (!window.confirm(`Encontramos um rascunho salvo automaticamente às ${nowLabel(savedAt)}. Deseja restaurar?`)) {
        return;
      }
      restoring = true;
      if (data.despesasHtml) {
        const corpoDesp = document.getElementById("corpo-despesas");
        if (corpoDesp) corpoDesp.innerHTML = data.despesasHtml;
      }
      if (data.trechosHtml) {
        const corpoKm = document.getElementById("corpo-trechos");
        if (corpoKm) corpoKm.innerHTML = data.trechosHtml;
      }
      applyEntries(form, data.entries);
      restoring = false;
      window.dispatchEvent(new CustomEvent("relatorio:autosave-restored"));
      dirty = true;
      setStatus("dirty", "Alterações não salvas");
      callOptional("atualizarBadgesAbas");
      callOptional("atualizarBadgesClientesRelatorio");
      callOptional("atualizarBadgesClientesItens");
      callOptional("recalcularTudo");
      scheduleServerSave();
    }

    function buildServerFormData() {
      const data = new FormData(form);
      data.set("autosave_key", autosaveKeyInput?.value || generatedKey);
      data.set("pagina_atual", window.location.href);
      data.set("relatorio_id", form.dataset.relatorioId || "");
      data.set("acao", "autosave");
      return data;
    }

    function buildKeepAliveBody() {
      const body = new URLSearchParams();
      const data = new FormData(form);
      data.forEach((value, key) => {
        if (value instanceof File) return;
        body.append(key, value);
      });
      body.set("autosave_key", autosaveKeyInput?.value || generatedKey);
      body.set("pagina_atual", window.location.href);
      body.set("relatorio_id", form.dataset.relatorioId || "");
      body.set("acao", "autosave");
      return body;
    }

    async function saveServer(options = {}) {
      if (!endpoint || saving || submitting) return false;
      saving = true;
      setStatus("saving", "Salvando...");
      try {
        const keepalive = Boolean(options.keepalive);
        const response = await fetch(endpoint, {
          method: "POST",
          body: keepalive ? buildKeepAliveBody() : buildServerFormData(),
          credentials: "same-origin",
          keepalive,
          headers: keepalive
            ? {
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "X-CSRFToken": getCsrf(form),
              }
            : { "X-CSRFToken": getCsrf(form) },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        dirty = false;
        lastServerSaveAt = Date.now();
        setStatus("saved", `Salvo automaticamente às ${payload.saved_at || nowLabel()}`);
        return true;
      } catch (error) {
        setStatus("error", "Não foi possível salvar automaticamente.");
        return false;
      } finally {
        saving = false;
      }
    }

    function scheduleLocalSave() {
      clearTimeout(localTimer);
      localTimer = setTimeout(saveLocal, LOCAL_DEBOUNCE_MS);
    }

    function scheduleServerSave() {
      clearTimeout(serverTimer);
      const elapsed = Date.now() - lastServerSaveAt;
      const wait = lastServerSaveAt ? Math.max(SERVER_DEBOUNCE_MS, SERVER_MIN_INTERVAL_MS - elapsed) : SERVER_DEBOUNCE_MS;
      serverTimer = setTimeout(() => {
        if (dirty) saveServer();
      }, wait);
    }

    function markDirty() {
      if (restoring || submitting) return;
      dirty = true;
      setStatus("dirty", "Alterações não salvas");
      scheduleLocalSave();
      scheduleServerSave();
    }

    form.addEventListener("input", markDirty, true);
    form.addEventListener("change", markDirty, true);
    form.addEventListener("autosave:dirty", markDirty);
    document.addEventListener("click", (event) => {
      if (event.target.closest("#btn-adicionar-despesa, #btn-adicionar-km, .btn-remover-despesa, .btn-remover-trecho, .cliente-opcao, .tecnico-opcao")) {
        setTimeout(markDirty, 150);
      }
    }, true);

    form.addEventListener("submit", () => {
      submitting = true;
      saveLocal();
    });

    window.addEventListener("beforeunload", (event) => {
      if (!dirty || submitting) return;
      saveLocal();
      saveServer({ keepalive: true });
      event.preventDefault();
      event.returnValue = "";
    });

    setInterval(() => {
      if (dirty && !saving && !submitting) saveServer();
    }, SERVER_MIN_INTERVAL_MS);

    restoreLocalIfWanted();
    if (!dirty) setStatus("saved", "Rascunho protegido");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
