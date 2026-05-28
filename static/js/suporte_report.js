(function () {
  function csrfToken() {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function showFeedback(type, message) {
    const box = document.getElementById("reportar-problema-feedback");
    if (!box) return;
    box.className = `alert alert-${type} mb-3`;
    box.textContent = message;
  }

  function clearFeedback() {
    const box = document.getElementById("reportar-problema-feedback");
    if (!box) return;
    box.className = "alert d-none mb-3";
    box.textContent = "";
  }

  function setLoading(loading) {
    const button = document.getElementById("btn-enviar-report");
    if (!button) return;
    button.disabled = loading;
    button.querySelector(".spinner-border")?.classList.toggle("d-none", !loading);
    const label = button.querySelector(".btn-label");
    if (label) label.textContent = loading ? "Enviando..." : "Enviar";
  }

  function validar(form) {
    let valido = true;
    const tipo = form.querySelector("[name='tipo']");
    const assunto = form.querySelector("[name='assunto']");
    const descricao = form.querySelector("[name='descricao']");

    [tipo, assunto, descricao].forEach((field) => field?.classList.remove("is-invalid"));

    if (!tipo?.value) {
      tipo?.classList.add("is-invalid");
      valido = false;
    }
    if (!assunto?.value.trim() || assunto.value.trim().length > 150) {
      assunto?.classList.add("is-invalid");
      valido = false;
    }
    const texto = descricao?.value.trim() || "";
    if (texto.length < 10 || texto.length > 3000) {
      descricao?.classList.add("is-invalid");
      valido = false;
    }
    return valido;
  }

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("form-reportar-problema");
    if (!form) return;

    const modal = document.getElementById("modalReportarProblema");
    modal?.addEventListener("hidden.bs.modal", function () {
      form.reset();
      form.querySelectorAll(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
      clearFeedback();
      setLoading(false);
    });

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      clearFeedback();
      if (!validar(form)) {
        showFeedback("warning", "Preencha os campos obrigatórios antes de enviar.");
        return;
      }

      setLoading(true);
      const payload = {
        tipo: form.querySelector("[name='tipo']").value,
        assunto: form.querySelector("[name='assunto']").value.trim(),
        descricao: form.querySelector("[name='descricao']").value.trim(),
        pagina_atual: window.location.href,
      };

      try {
        const response = await fetch(form.dataset.reportUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
          },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
          throw new Error(data.error || "Não foi possível enviar a mensagem.");
        }
        showFeedback("success", data.message || "Mensagem enviada com sucesso.");
        window.setTimeout(function () {
          window.bootstrap?.Modal.getOrCreateInstance(document.getElementById("modalReportarProblema")).hide();
        }, 900);
      } catch (error) {
        showFeedback(
          "danger",
          error.message || "Não foi possível enviar a mensagem. Tente novamente ou contate o suporte."
        );
      } finally {
        setLoading(false);
      }
    });
  });
})();
