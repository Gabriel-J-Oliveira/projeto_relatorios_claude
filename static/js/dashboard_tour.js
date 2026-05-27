(function () {
  const STORAGE_KEY = "dashboardTourVisto:v1";

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

  function element(selector) {
    return document.querySelector(selector);
  }

  function buildSteps() {
    const candidates = [
      {
        element: "#dashboard-root",
        popover: {
          title: "Bem-vindo ao Dashboard",
          description:
            "Esta tela reúne os principais indicadores do sistema de reembolsos. Aqui você acompanha relatórios, valores, gráficos e pendências conforme suas permissões.",
          side: "bottom",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-filtros"]',
        popover: {
          title: "Filtros da análise",
          description:
            "Use os filtros para ajustar o período, cliente, técnico ou status. Todos os cards e gráficos são atualizados com base nesses critérios.",
          side: "bottom",
          align: "start",
        },
      },
      {
        element: '[data-tour="dashboard-kpis"]',
        popover: {
          title: "Indicadores principais",
          description:
            "Estes cards mostram um resumo rápido dos valores, relatórios pendentes, aprovações e quilometragem.",
          side: "bottom",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-evolucao-financeira"]',
        popover: {
          title: "Evolução financeira",
          description:
            "Este gráfico mostra a evolução dos valores solicitados, aprovados e removidos ao longo do período selecionado.",
          side: "top",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-relatorios-recentes"]',
        popover: {
          title: "Relatórios recentes",
          description:
            "Aqui ficam os relatórios mais recentes para acesso rápido. Use esta área para acompanhar movimentações novas.",
          side: "right",
          align: "start",
        },
      },
      {
        element: '[data-tour="dashboard-gastos-cliente"]',
        popover: {
          title: "Gastos por cliente",
          description:
            "Este gráfico mostra os clientes com maior volume financeiro no período filtrado.",
          side: "top",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-relatorios-tecnico"]',
        popover: {
          title: "Análise por técnico",
          description:
            "Esta área ajuda a visualizar volume de relatórios, valores ou KM por técnico, conforme seu perfil de acesso.",
          side: "top",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-km-tecnico"]',
        popover: {
          title: "KM por técnico",
          description:
            "Este bloco mostra a quilometragem consolidada ou a evolução do seu próprio KM, dependendo do seu perfil.",
          side: "top",
          align: "center",
        },
      },
      {
        element: '[data-tour="dashboard-status"]',
        popover: {
          title: "Status dos relatórios",
          description:
            "Este gráfico mostra a distribuição dos relatórios por status, como conferência pendente, ajuste, aprovado ou rejeitado.",
          side: "left",
          align: "start",
        },
      },
      {
        element: "#dashboard-tour-start",
        popover: {
          title: "Pronto!",
          description:
            "Você pode refazer este guia a qualquer momento clicando em Ver guia.",
          side: "bottom",
          align: "center",
        },
      },
    ];

    return candidates.filter((step) => !step.element || element(step.element));
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
      popoverClass: "dashboard-driver-popover",
      steps,
      onDestroyed: markSeen,
    });

    if (force || !hasSeen()) {
      if (!force) markSeen();
      driverObj.drive();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("dashboard-tour-start");
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
