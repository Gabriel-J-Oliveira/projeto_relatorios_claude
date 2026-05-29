(function () {
  function csrfToken() {
    return document.cookie
      .split(";")
      .map(part => part.trim())
      .find(part => part.startsWith("csrftoken="))
      ?.split("=")[1] || "";
  }

  function modalElement() {
    return document.getElementById("modalClientesValorKm");
  }

  function abrirModal() {
    const modal = modalElement();
    if (!modal) return;
    bootstrap.Modal.getOrCreateInstance(modal).show();
  }

  function iniciarModalAviso() {
    const modal = modalElement();
    if (!modal) return;
    document.querySelectorAll("[data-open-clientes-valor-km]").forEach(btn => {
      btn.addEventListener("click", abrirModal);
    });
    const pendencias = parseInt(modal.dataset.pendencias || "0", 10);
    if (pendencias > 0 && sessionStorage.getItem("clientesValorKmLembrarDepois") !== "1") {
      setTimeout(abrirModal, 500);
    }
    modal.addEventListener("hide.bs.modal", () => {
      sessionStorage.setItem("clientesValorKmLembrarDepois", "1");
    });
  }

  function feedback(row, message, success) {
    const el = row.querySelector("[data-valor-km-feedback]");
    if (!el) return;
    el.textContent = message || "";
    el.classList.toggle("text-success", Boolean(success));
    el.classList.toggle("text-danger", !success);
  }

  async function salvarValorKmLinha(button) {
    const row = button.closest("[data-cliente-row]");
    if (!row) return;
    const input = row.querySelector("[data-cliente-valor-km-input]");
    const valor = input?.value || "";
    if (!valor.trim()) {
      feedback(row, "Informe o valor de KM.", false);
      return;
    }
    button.disabled = true;
    const htmlOriginal = button.innerHTML;
    button.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    try {
      const response = await fetch(row.dataset.valorKmUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ valor_km: valor }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || "Não foi possível salvar.");
      }
      feedback(row, data.message || "Valor salvo.", true);
      if (new URLSearchParams(window.location.search).get("valor_km") === "pendente") {
        row.classList.add("table-success");
        setTimeout(() => row.remove(), 450);
      } else if (data.valor_km) {
        input.value = data.valor_km;
      }
    } catch (error) {
      feedback(row, error.message || "Não foi possível salvar.", false);
    } finally {
      button.disabled = false;
      button.innerHTML = htmlOriginal;
    }
  }

  function iniciarEdicaoRapida() {
    document.querySelectorAll("[data-save-cliente-valor-km]").forEach(button => {
      button.addEventListener("click", () => salvarValorKmLinha(button));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    iniciarModalAviso();
    iniciarEdicaoRapida();
  });
})();
