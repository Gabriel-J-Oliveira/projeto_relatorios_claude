(function () {
  const allowedExt = [".pdf", ".jpg", ".jpeg", ".png"];
  const allowedMime = ["application/pdf", "image/jpeg", "image/png"];
  const message = "Formato não permitido. Envie apenas PDF, JPG, JPEG ou PNG.";

  function extFromName(name) {
    const lower = String(name || "").toLowerCase();
    const dot = lower.lastIndexOf(".");
    return dot >= 0 ? lower.slice(dot) : "";
  }

  function isAllowedFile(file) {
    if (!file) return true;
    const ext = extFromName(file.name);
    const type = file.type || "";
    return allowedExt.includes(ext) && (!type || allowedMime.includes(type));
  }

  function showInputError(input, text) {
    input.classList.add("is-invalid");
    const wrapper = input.closest("td, .mb-3, .form-group, label") || input.parentElement;
    const feedback = wrapper?.parentElement?.querySelector(".erro-inline")
      || wrapper?.querySelector(".erro-inline")
      || input.parentElement?.querySelector(".invalid-feedback");
    if (feedback) {
      feedback.textContent = text;
      feedback.classList.add("d-block");
    } else {
      window.alert(text);
    }
  }

  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    const file = input.files && input.files[0];
    if (isAllowedFile(file)) {
      input.classList.remove("is-invalid");
      return;
    }
    input.value = "";
    showInputError(input, message);
  });

  function renderPreview(body, url, fileType, fileName) {
    body.innerHTML = "";
    const type = String(fileType || "").toLowerCase();
    const ext = extFromName(fileName);

    if (type === "application/pdf" || ext === ".pdf") {
      const iframe = document.createElement("iframe");
      iframe.src = url;
      iframe.className = "anexo-preview-frame";
      iframe.title = fileName || "Pré-visualização do PDF";
      body.appendChild(iframe);
      return;
    }

    if (type.startsWith("image/") || [".jpg", ".jpeg", ".png"].includes(ext)) {
      const img = document.createElement("img");
      img.src = url;
      img.alt = fileName || "Pré-visualização do anexo";
      img.className = "anexo-preview-image";
      body.appendChild(img);
      return;
    }

    const empty = document.createElement("div");
    empty.className = "alert alert-warning mb-0";
    empty.textContent = "Não foi possível pré-visualizar este arquivo.";
    body.appendChild(empty);
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".anexo-preview-btn");
    if (!button) return;
    event.preventDefault();

    const modalEl = document.getElementById("modalAnexoPreview");
    const body = document.getElementById("anexo-preview-body");
    const meta = document.getElementById("anexo-preview-meta");
    const download = document.getElementById("anexo-preview-download");
    const open = document.getElementById("anexo-preview-open");
    if (!modalEl || !body || !download || !open) return;

    const previewUrl = button.dataset.previewUrl || "";
    const downloadUrl = button.dataset.downloadUrl || previewUrl;
    const fileName = button.dataset.fileName || "anexo";
    const fileType = button.dataset.fileType || "";

    meta.textContent = fileType ? `${fileName} · ${fileType}` : fileName;
    download.href = downloadUrl;
    open.href = previewUrl;
    renderPreview(body, previewUrl, fileType, fileName);

    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  });

  document.addEventListener("hidden.bs.modal", (event) => {
    if (event.target?.id !== "modalAnexoPreview") return;
    const body = document.getElementById("anexo-preview-body");
    if (body) body.innerHTML = "";
  });
})();
