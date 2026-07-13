(function () {
  const TOURS = {
    anexos: "relatorioNovidadeMultiplosAnexos:v1",
    hospedagem: "relatorioNovidadeHospedagemPeriodo:v1",
    tecnicos: "relatorioNovidadeMultiplosTecnicos:v1",
    cidades: "relatorioNovidadeMultiplasCidades:v1",
  };

  const fila = [];
  let executando = false;

  function storageKey(chave) {
    const userId = document.body?.dataset?.userId || "anon";
    return `${chave}:${userId}`;
  }

  function vistosServidor() {
    try {
      return JSON.parse(document.body?.dataset?.toursVistos || "{}");
    } catch (error) {
      return {};
    }
  }

  function foiVisto(chave) {
    try {
      return Boolean(vistosServidor()[chave]) || window.localStorage.getItem(storageKey(chave)) === "true";
    } catch (error) {
      return true;
    }
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function marcarVisto(chave) {
    try {
      window.localStorage.setItem(storageKey(chave), "true");
      const vistos = vistosServidor();
      vistos[chave] = true;
      document.body.dataset.toursVistos = JSON.stringify(vistos);
    } catch (error) {}

    const url = document.body?.dataset?.tourSeenUrl;
    if (!url || !window.fetch) return;
    window.fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({ tour: chave }),
    }).catch(function () {});
  }

  function driverFactory() {
    return window.driver && window.driver.js && window.driver.js.driver;
  }

  function find(selector) {
    return document.querySelector(selector);
  }

  function activateTab(tabSelector) {
    const trigger = find(tabSelector);
    if (!trigger || !window.bootstrap) return;
    window.bootstrap.Tab.getOrCreateInstance(trigger).show();
  }

  function scrollToElement(selector) {
    const target = find(selector);
    if (!target) return;
    window.setTimeout(function () {
      target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    }, 120);
  }

  function step(element, tabSelector, title, description, side) {
    return {
      element,
      tabSelector,
      popover: {
        title,
        description,
        side: side || "bottom",
        align: "center",
      },
    };
  }

  function driverAberto() {
    return Boolean(document.querySelector(".driver-popover, .driver-overlay"));
  }

  function executarTour(config) {
    const factory = driverFactory();
    if (!factory || !config.steps.length || foiVisto(config.key)) {
      executarProximo();
      return;
    }

    const steps = config.steps.filter((item) => find(item.element));
    if (!steps.length) {
      executarProximo();
      return;
    }

    if (driverAberto()) {
      window.setTimeout(function () {
        fila.unshift(config);
        executarProximo();
      }, 1200);
      return;
    }

    executando = true;
    const driverObj = factory({
      showProgress: true,
      allowClose: true,
      overlayClickBehavior: "close",
      disableActiveInteraction: true,
      showButtons: ["previous", "next", "close"],
      stagePadding: 8,
      stageRadius: 12,
      nextBtnText: "Próximo",
      prevBtnText: "Voltar",
      doneBtnText: "Finalizar",
      closeBtnText: "Pular tour",
      popoverClass: "relatorio-form-driver-popover",
      steps,
      onHighlightStarted: function (_element, activeStep) {
        activateTab(activeStep.tabSelector);
        scrollToElement(activeStep.element);
      },
      onDestroyed: function () {
        marcarVisto(config.key);
        executando = false;
        window.setTimeout(executarProximo, 800);
      },
    });
    driverObj.drive();
  }

  function enfileirar(config) {
    if (!config || foiVisto(config.key)) return;
    if (fila.some((item) => item.key === config.key)) return;
    fila.push(config);
    executarProximo();
  }

  function executarProximo() {
    if (executando) return;
    const proximo = fila.shift();
    if (!proximo) return;
    executarTour(proximo);
  }

  function tourMultiplasCidades() {
    enfileirar({
      key: TOURS.cidades,
      steps: [
        step(
          '[data-tour="relatorio-cidades-atendidas"]',
          "#tab-dados-btn",
          "Várias cidades no relatório",
          "Agora o relatório pode registrar mais de uma cidade atendida na mesma viagem."
        ),
        step(
          "#btn-adicionar-cidade",
          "#tab-dados-btn",
          "Adicionar cidade",
          "Use este botão para incluir quantas cidades forem necessárias."
        ),
        step(
          '[data-tour="relatorio-periodo"]',
          "#tab-dados-btn",
          "Dados da viagem",
          "Data inicial, data final e adiantamento ficam separados das cidades atendidas."
        ),
      ],
    });
  }

  function tourMultiplosAnexos() {
    enfileirar({
      key: TOURS.anexos,
      steps: [
        step(
          '[data-tour="despesas-comprovante"]',
          "#tab-despesas-btn",
          "Vários comprovantes",
          "Agora uma mesma despesa pode ter mais de um comprovante."
        ),
        step(
          '[data-tour="despesas-comprovante"] .despesa-file-btn',
          "#tab-despesas-btn",
          "Adicionar anexos",
          "Use Adicionar para incluir novos arquivos sem substituir os comprovantes já salvos."
        ),
        step(
          '[data-tour="despesas-comprovante"]',
          "#tab-despesas-btn",
          "Envio conjunto",
          "Todos os comprovantes válidos serão enviados junto com o relatório."
        ),
      ],
    });
  }

  function tourMultiplosTecnicos() {
    enfileirar({
      key: TOURS.tecnicos,
      steps: [
        step(
          '[data-tour="despesas-tecnicos"]',
          "#tab-despesas-btn",
          "Técnicos por despesa",
          "Agora vários técnicos podem participar da mesma despesa."
        ),
        step(
          '[data-tour="despesas-tecnicos"] button',
          "#tab-despesas-btn",
          "Selecionar participantes",
          "A seleção usa a mesma lógica dos clientes da despesa."
        ),
      ],
    });
  }

  function existeHospedagem() {
    return Array.from(document.querySelectorAll('.linha-despesa [name$="-tipo"]'))
      .some((campo) => campo.value === "hospedagem");
  }

  function tourHospedagem() {
    if (!existeHospedagem()) return;
    enfileirar({
      key: TOURS.hospedagem,
      steps: [
        step(
          '[data-tour="despesas-data-tipo"]',
          "#tab-despesas-btn",
          "Hospedagem por período",
          "Para hospedagem, informe o período de permanência."
        ),
        step(
          '[data-tour="despesas-hospedagem-entrada"]',
          "#tab-despesas-btn",
          "Data de entrada",
          "Informe a data inicial da hospedagem."
        ),
        step(
          '[data-tour="despesas-hospedagem-saida"]',
          "#tab-despesas-btn",
          "Data de saída",
          "Informe a data final. O sistema calcula as diárias automaticamente."
        ),
        step(
          '[data-tour="despesas-hospedagem-diarias"]',
          "#tab-despesas-btn",
          "Política por diária",
          "A política considera o valor diário multiplicado pela quantidade de diárias."
        ),
      ],
    });
  }

  function aoAbrirDespesas() {
    window.setTimeout(function () {
      tourMultiplosAnexos();
      tourMultiplosTecnicos();
      tourHospedagem();
    }, 400);
  }

  document.addEventListener("DOMContentLoaded", function () {
    window.setTimeout(tourMultiplasCidades, 1200);

    const tabDespesas = document.getElementById("tab-despesas-btn");
    tabDespesas?.addEventListener("shown.bs.tab", aoAbrirDespesas);

    document.getElementById("corpo-despesas")?.addEventListener("change", function (event) {
      if (event.target.matches('[name$="-tipo"]') && event.target.value === "hospedagem") {
        window.setTimeout(tourHospedagem, 300);
      }
    });

    if (document.querySelector("#tab-despesas.show.active")) {
      aoAbrirDespesas();
    }
  });
})();
