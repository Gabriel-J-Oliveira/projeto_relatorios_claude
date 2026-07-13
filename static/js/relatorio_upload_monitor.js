(function () {
  "use strict";

  const PERMITTED_EXTENSIONS = new Set(["pdf", "jpg", "jpeg", "png"]);
  const PERMITTED_MIMES = new Set(["application/pdf", "image/jpeg", "image/png", ""]);
  const BYTES_IN_MB = 1024 * 1024;
  const ESTIMATED_UPLOAD_MBPS = 4;

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= BYTES_IN_MB) return `${(value / BYTES_IN_MB).toFixed(1).replace(".", ",")} MB`;
    if (value >= 1024) return `${(value / 1024).toFixed(0)} KB`;
    return `${value} B`;
  }

  function fileExtension(name) {
    const clean = String(name || "").split("?")[0].split("#")[0];
    const pieces = clean.split(".");
    return pieces.length > 1 ? pieces.pop().toLowerCase() : "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function statusLine(input) {
    const targetId = input.dataset.uploadStatusTarget;
    if (targetId) {
      const target = document.getElementById(targetId);
      if (target) return target;
    }
    return input.closest(".despesa-field-anexo")?.querySelector("[data-upload-status-line]");
  }

  function setStatus(input, state, message, icon) {
    const line = statusLine(input);
    if (!line) return;
    line.dataset.uploadState = state;
    line.classList.remove("text-muted", "text-success", "text-warning", "text-danger", "text-info");
    const color = {
      empty: "text-muted",
      persisted: "text-success",
      pending: "text-info",
      sending: "text-info",
      error: "text-danger",
      rejected: "text-danger",
    }[state] || "text-muted";
    line.classList.add(color);
    line.innerHTML = `${icon ? `<i class="bi ${icon} me-1"></i>` : ""}${message}`;
  }

  function setButtonState(input, state) {
    const button = input.closest(".despesa-file-btn");
    if (!button) return;
    button.classList.remove(
      "btn-outline-secondary",
      "btn-outline-success",
      "btn-outline-danger",
      "btn-outline-warning",
      "btn-success",
      "btn-danger",
      "btn-warning"
    );
    const label = button.querySelector(".despesa-file-label");
    if (state === "rejected" || state === "error") {
      button.classList.add("btn-outline-danger");
      if (label) label.textContent = "Corrigir";
    } else if (state === "sending") {
      button.classList.add("btn-outline-primary");
      if (label) label.textContent = "Enviando";
    } else if (state === "pending") {
      button.classList.add("btn-outline-warning");
      if (label) label.textContent = "Aguardando envio";
    } else {
      button.classList.add("btn-outline-secondary");
      if (label) label.textContent = "Anexar";
    }
  }

  function fileKey(file) {
    return [
      file.name || "",
      file.size || 0,
      file.lastModified || 0,
      file.type || "",
    ].join("|");
  }

  function ensureTransfer(input) {
    if (!input._relatorioUploadTransfer) {
      input._relatorioUploadTransfer = new DataTransfer();
    }
    return input._relatorioUploadTransfer;
  }

  function acumularArquivosSelecionados(input) {
    if (!window.DataTransfer || !input.multiple) return;
    const transfer = ensureTransfer(input);
    const existentes = new Set(Array.from(transfer.files || []).map(fileKey));
    Array.from(input.files || []).forEach((file) => {
      const key = fileKey(file);
      if (existentes.has(key)) return;
      transfer.items.add(file);
      existentes.add(key);
    });
    input.files = transfer.files;
  }

  function selectedList(input) {
    return input.closest(".despesa-field-anexo")?.querySelector("[data-upload-selected-list]");
  }

  function renderSelectedList(input) {
    const list = selectedList(input);
    if (!list) return;
    const files = Array.from(input.files || []);
    list.classList.toggle("d-none", files.length === 0);
    list.innerHTML = files.map((file, index) => (
      `<div class="d-flex align-items-center justify-content-between gap-2 upload-selected-item">` +
      `<span class="text-truncate"><i class="bi bi-paperclip me-1"></i>${escapeHtml(file.name)}</span>` +
      `<span class="text-muted flex-shrink-0">${formatBytes(file.size || 0)}</span>` +
      `</div>`
    )).join("");
  }

  function validateFile(file) {
    if (!file) return { ok: true };
    if (file.size <= 0) {
      return { ok: false, state: "rejected", message: "Arquivo vazio.", reason: "arquivo_vazio" };
    }
    const ext = fileExtension(file.name);
    if (!PERMITTED_EXTENSIONS.has(ext) || !PERMITTED_MIMES.has(file.type || "")) {
      return {
        ok: false,
        state: "rejected",
        message: "Arquivo rejeitado. Envie apenas PDF, JPG, JPEG ou PNG.",
        reason: "tipo_invalido",
      };
    }
    return { ok: true };
  }

  function collectFiles(form) {
    const items = [];
    const errors = [];
    form.querySelectorAll('input[type="file"][data-upload-comprovante]').forEach((input) => {
      const files = Array.from(input.files || []);
      renderSelectedList(input);
      files.forEach((file) => {
        const validation = validateFile(file);
        const item = {
          input,
          file,
          name: file.name,
          size: file.size || 0,
          validation,
        };
        items.push(item);
        if (!validation.ok) errors.push(item);
      });
    });
    return { items, errors };
  }

  function updateMonitor(form) {
    const maxTotalMb = Number(form.dataset.uploadMaxTotalMb || "1024") || 1024;
    const existingBytes = Number(form.dataset.uploadExistingBytes || "0") || 0;
    const maxTotalBytes = maxTotalMb * BYTES_IN_MB;
    const { items, errors } = collectFiles(form);
    const selectedBytes = items.reduce((sum, item) => sum + item.size, 0);
    const totalBytes = existingBytes + selectedBytes;
    const percent = maxTotalBytes ? Math.min(100, (totalBytes / maxTotalBytes) * 100) : 0;

    items.forEach((item) => {
      if (!item.validation.ok) {
        setStatus(item.input, item.validation.state, item.validation.message, "bi-x-circle");
        setButtonState(item.input, item.validation.state);
      } else {
        const totalInputFiles = item.input.files ? item.input.files.length : 1;
        const mensagem = totalInputFiles > 1
          ? `${totalInputFiles} arquivos prontos para envio`
          : `${item.name} pronto para envio (${formatBytes(item.size)})`;
        setStatus(item.input, "pending", mensagem, "bi-clock-history");
        setButtonState(item.input, "pending");
      }
    });

    const card = document.querySelector("[data-upload-monitor-card]");
    if (!card) return { items, errors, totalBytes, maxTotalBytes, overLimit: totalBytes > maxTotalBytes };

    const count = card.querySelector("[data-upload-count]");
    const size = card.querySelector("[data-upload-size]");
    const time = card.querySelector("[data-upload-time]");
    const summary = card.querySelector("[data-upload-summary]");
    const status = card.querySelector("[data-upload-status]");
    const progress = card.querySelector("[data-upload-progress]");
    const alert = card.querySelector("[data-upload-alert]");
    const largestWrap = card.querySelector("[data-upload-largest-wrap]");
    const largest = card.querySelector("[data-upload-largest]");

    const persistedCount = form.querySelectorAll('[data-upload-status-line][data-upload-state="persisted"]').length;
    if (count) {
      const totalCount = persistedCount + items.length;
      count.textContent = `${totalCount} anexo${totalCount === 1 ? "" : "s"}`;
    }
    if (size) size.textContent = `${formatBytes(totalBytes)} de ${formatBytes(maxTotalBytes)}`;
    if (time) {
      const seconds = selectedBytes ? Math.max(1, Math.ceil((selectedBytes / BYTES_IN_MB) / ESTIMATED_UPLOAD_MBPS)) : 0;
      time.textContent = seconds ? `~${seconds}s` : "-";
    }
    if (summary) {
      if (items.length) {
        summary.textContent = `${items.length} arquivo(s) novo(s) aguardando envio. Sucesso só será confirmado após salvar o relatório.`;
      } else if (persistedCount) {
        summary.textContent = `${persistedCount} comprovante(s) já persistido(s) no relatório.`;
      } else {
        summary.textContent = "Nenhum anexo novo selecionado.";
      }
    }
    if (progress) {
      progress.style.width = `${Math.min(100, percent).toFixed(1)}%`;
      progress.className = "progress-bar";
      if (totalBytes > maxTotalBytes) progress.classList.add("bg-danger");
      else if (percent >= 90) progress.classList.add("bg-warning");
      else if (percent >= 70) progress.classList.add("bg-info");
      else progress.classList.add("bg-success");
    }
    if (status) {
      status.className = "badge rounded-pill";
      if (errors.length || totalBytes > maxTotalBytes) {
        status.classList.add("text-bg-danger");
        status.textContent = totalBytes > maxTotalBytes ? "Acima do limite" : "Revisar anexos";
      } else if (percent >= 90) {
        status.classList.add("text-bg-warning");
        status.textContent = "Próximo do limite";
      } else if (percent >= 70) {
        status.classList.add("text-bg-info");
        status.textContent = "Relatório grande";
      } else {
        status.classList.add("text-bg-success");
        status.textContent = "Dentro do limite";
      }
    }
    if (alert) {
      let message = "";
      if (errors.length) message = "Há anexos rejeitados. Corrija antes de salvar ou enviar.";
      else if (totalBytes > maxTotalBytes) message = "O total de anexos excede o limite permitido. Remova arquivos grandes.";
      else if (percent >= 90) message = "O relatório está próximo do limite de anexos.";
      else if (percent >= 70) message = "O relatório está ficando grande. Revise imagens muito pesadas.";
      alert.textContent = message;
      alert.classList.toggle("d-none", !message);
    }
    if (largestWrap && largest) {
      const ordered = [...items].sort((a, b) => b.size - a.size).slice(0, 5);
      largestWrap.classList.toggle("d-none", ordered.length === 0);
      largest.innerHTML = ordered.map((item) => (
        `<div class="upload-largest-item d-flex justify-content-between gap-2">` +
        `<span class="text-truncate">${escapeHtml(item.name)}</span>` +
        `<strong>${formatBytes(item.size)}</strong>` +
        `</div>`
      )).join("");
    }

    return { items, errors, totalBytes, maxTotalBytes, overLimit: totalBytes > maxTotalBytes };
  }

  function showFormError(form, message) {
    let box = form.querySelector("[data-upload-form-error]");
    if (!box) {
      box = document.createElement("div");
      box.className = "alert alert-danger border-0 shadow-sm mb-3";
      box.dataset.uploadFormError = "1";
      const firstPane = form.querySelector(".tab-content") || form;
      firstPane.parentNode.insertBefore(box, firstPane);
    }
    box.textContent = message;
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function init() {
    const form = document.getElementById("form-relatorio");
    if (!form) return;

    form.addEventListener("change", (event) => {
      const input = event.target.closest('input[type="file"][data-upload-comprovante]');
      if (!input) return;
      acumularArquivosSelecionados(input);
      updateMonitor(form);
    });

    form.addEventListener("click", (event) => {
      if (event.target.closest("[onclick*='removerLinha']")) {
        setTimeout(() => updateMonitor(form), 50);
      }
    });

    form.addEventListener("submit", (event) => {
      const state = updateMonitor(form);
      if (state.errors.length || state.overLimit) {
        event.preventDefault();
        const names = state.errors.map((item) => item.name).slice(0, 4).join(", ");
        showFormError(
          form,
          names
            ? `Revise os anexos antes de continuar: ${names}.`
            : "O total de anexos excede o limite permitido. Remova arquivos grandes antes de continuar."
        );
        return;
      }
      window.setTimeout(() => {
        if (event.defaultPrevented) return;
        state.items.forEach((item) => {
          setStatus(item.input, "sending", "Enviando...", "bi-hourglass-split");
          setButtonState(item.input, "sending");
        });
      }, 0);
    }, true);

    document.addEventListener("relatorio:linha-adicionada", () => updateMonitor(form));
    updateMonitor(form);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
