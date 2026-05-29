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

  function feedback(text, isError = true) {
    const el = document.getElementById("clientes-valor-km-feedback");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("text-danger", isError);
    el.classList.toggle("text-success", !isError);
  }

  function coletarItens() {
    return Array.from(document.querySelectorAll("[data-cliente-valor-km-row]"))
      .map(row => {
        const valor = row.querySelector("[data-cliente-valor-km-input]")?.value || "";
        return {
          cliente_id: row.dataset.clienteId,
          valor_km: valor,
        };
      })
      .filter(item => String(item.valor_km || "").trim());
  }

  async function salvarValores() {
    const modal = modalElement();
    const botao = document.getElementById("btnSalvarClientesValorKm");
    if (!modal || !botao) return;
    const itens = coletarItens();
    if (!itens.length) {
      feedback("Informe ao menos um valor de KM para salvar.");
      return;
    }
    botao.disabled = true;
    const textoOriginal = botao.innerHTML;
    botao.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Salvando...';
    feedback("");
    try {
      const response = await fetch(modal.dataset.saveUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ clientes: itens }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || "Não foi possível salvar os valores.");
      }
      feedback(data.message || "Valores salvos com sucesso.", false);
      setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      feedback(error.message || "Não foi possível salvar os valores.");
    } finally {
      botao.disabled = false;
      botao.innerHTML = textoOriginal;
    }
  }

  function abrirModal() {
    const modal = modalElement();
    if (!modal) return;
    bootstrap.Modal.getOrCreateInstance(modal).show();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const modal = modalElement();
    if (!modal) return;
    document.querySelectorAll("[data-open-clientes-valor-km]").forEach(btn => {
      btn.addEventListener("click", abrirModal);
    });
    document.getElementById("btnSalvarClientesValorKm")?.addEventListener("click", salvarValores);
    const pendencias = parseInt(modal.dataset.pendencias || "0", 10);
    if (pendencias > 0 && sessionStorage.getItem("clientesValorKmLembrarDepois") !== "1") {
      setTimeout(abrirModal, 500);
    }
    modal.addEventListener("hide.bs.modal", () => {
      sessionStorage.setItem("clientesValorKmLembrarDepois", "1");
    });
  });
})();
