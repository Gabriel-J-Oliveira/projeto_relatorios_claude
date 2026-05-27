(function () {
  const STORAGE_KEY = "relatoriosListTourVisto:v1";

  function storageKey() {
    const userId = document.body?.dataset?.userId || "anon";
    return `${STORAGE_KEY}:${userId}`;
  }

  function seenOnServer() {
    try {
      const seen = JSON.parse(document.body?.dataset?.toursVistos || "{}");
      return Boolean(seen[STORAGE_KEY]);
    } catch (error) {
      return false;
    }
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function syncSeen() {
    const url = document.body?.dataset?.tourSeenUrl;
    if (!url || !window.fetch) return;
    window.fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({ tour: STORAGE_KEY }),
    }).catch(function () {});
  }

  function getDriverFactory() {
    return window.driver && window.driver.js && window.driver.js.driver;
  }

  function find(selector) {
    return document.querySelector(selector);
  }

  function step(element, title, description, side, align) {
    return {
      element,
      popover: {
        title,
        description,
        side: side || "bottom",
        align: align || "center",
      },
    };
  }

  function buildSteps() {
    const candidates = [
      step(
        '[data-tour="relatorios-list-tabela"]',
        "Listagem de relatórios",
        "Nesta tela você consulta relatórios, aplica filtros, acompanha status e acessa ações permitidas para cada relatório.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-novo"]',
        "Novo relatório",
        "Use este botão para iniciar um novo relatório de reembolso.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-filtros"]',
        "Filtros da listagem",
        "Use os filtros para localizar relatórios por período, cliente, técnico, status ou outros critérios disponíveis.",
        "bottom",
        "start"
      ),
      step(
        '[data-tour="relatorios-list-busca"]',
        "Busca rápida",
        "Utilize a busca para encontrar relatórios pelo número, cliente, técnico ou informações relacionadas.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-status"]',
        "Filtro por status",
        "Filtre relatórios por situação, como rascunho, conferência pendente, ajuste pendente, aprovado ou rejeitado.",
        "bottom"
      ),
      step(
        ".relatorios-list-table-wrap",
        "Tabela de relatórios",
        "Aqui são exibidos os relatórios encontrados conforme os filtros aplicados.",
        "top"
      ),
      step(
        '[data-tour="relatorios-list-numero"]',
        "Número do relatório",
        "O número identifica o relatório. Clique no cabeçalho para ordenar em ordem crescente ou decrescente, quando disponível.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-clientes-linha"], [data-tour="relatorios-list-clientes"]',
        "Clientes do relatório",
        "Esta coluna mostra o cliente principal. Quando houver múltiplos clientes, utilize o indicador exibido para visualizar os demais.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-status-linha"], [data-tour="relatorios-list-status-coluna"]',
        "Status do relatório",
        "O status indica em que etapa o relatório está: rascunho, conferência, ajuste, aprovado ou rejeitado.",
        "bottom"
      ),
      step(
        '[data-tour="relatorios-list-acoes-linha"], [data-tour="relatorios-list-acoes"]',
        "Ações disponíveis",
        "Os botões desta coluna permitem visualizar, editar, duplicar ou acessar documentos, conforme seu perfil e o status do relatório.",
        "left"
      ),
      step(
        '[data-tour="relatorios-list-paginacao"]',
        "Paginação",
        "Use a paginação para navegar entre os resultados quando houver muitos relatórios.",
        "top"
      ),
      step(
        "#relatorios-list-tour-start",
        "Pronto!",
        "Você pode refazer este guia a qualquer momento clicando em Ver guia.",
        "bottom"
      ),
    ];

    return candidates.filter((item) => find(item.element));
  }

  function markSeen() {
    try {
      window.localStorage.setItem(storageKey(), "true");
      const seen = JSON.parse(document.body?.dataset?.toursVistos || "{}");
      seen[STORAGE_KEY] = true;
      document.body.dataset.toursVistos = JSON.stringify(seen);
    } catch (error) {
      // Sem localStorage disponível, apenas não persiste o estado do tour.
    }
    syncSeen();
  }

  function hasSeen() {
    try {
      return seenOnServer() || window.localStorage.getItem(storageKey()) === "true";
    } catch (error) {
      return true;
    }
  }

  function startTour(force) {
    const driverFactory = getDriverFactory();
    if (!driverFactory) return;

    const steps = buildSteps();
    if (!steps.length) return;

    const driverObj = driverFactory({
      showProgress: true,
      allowClose: false,
      overlayClickBehavior: function () {},
      disableActiveInteraction: true,
      showButtons: ["previous", "next", "close"],
      stagePadding: 8,
      stageRadius: 12,
      nextBtnText: "Próximo",
      prevBtnText: "Voltar",
      doneBtnText: "Finalizar",
      closeBtnText: "Finalizar",
      popoverClass: "relatorios-list-driver-popover",
      steps,
      onHighlightStarted: function (element) {
        if (element && element.scrollIntoView) {
          element.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
        }
      },
      onDestroyed: markSeen,
    });

    if (force || !hasSeen()) {
      if (!force) markSeen();
      driverObj.drive();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("relatorios-list-tour-start");
    if (button) {
      button.addEventListener("click", function () {
        startTour(true);
      });
    }

    window.setTimeout(function () {
      startTour(false);
    }, 650);
  });
})();
