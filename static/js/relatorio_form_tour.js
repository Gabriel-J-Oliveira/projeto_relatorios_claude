(function () {
  const STORAGE_KEY = "relatorioFormTourVisto:v1";

  function getDriverFactory() {
    return window.driver && window.driver.js && window.driver.js.driver;
  }

  function find(selector) {
    return document.querySelector(selector);
  }

  function activateTab(tabSelector) {
    if (!tabSelector) return;
    const trigger = find(tabSelector);
    if (!trigger || !window.bootstrap) return;
    window.bootstrap.Tab.getOrCreateInstance(trigger).show();
  }

  function scrollToElement(selector) {
    const target = find(selector);
    if (!target) return;
    window.setTimeout(function () {
      target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      const map = find("#mapa-km-formulario");
      if (map && map.offsetParent !== null) {
        window.dispatchEvent(new Event("resize"));
      }
    }, 120);
  }

  function step(element, tabSelector, title, description, side, align) {
    return {
      element,
      tabSelector,
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
        "#form-relatorio",
        "#tab-dados-btn",
        "Cadastro de relatório",
        "Este guia mostra as principais etapas para preencher e enviar um relatório de reembolso.",
        "bottom"
      ),
      step(
        '[data-tour="relatorio-tipo"]',
        "#tab-dados-btn",
        "Tipo de relatório",
        "Selecione o tipo do relatório conforme o objetivo do atendimento, como operacional ou institucional."
      ),
      step(
        '[data-tour="relatorio-centro-custo"]',
        "#tab-dados-btn",
        "Centro de custo",
        "Informe a classificação ou centro de custo relacionado ao relatório."
      ),
      step(
        '[data-tour="relatorio-cidade-uf"]',
        "#tab-dados-btn",
        "Cidade, UF e localidade",
        "Informe a cidade e o estado onde ocorreu o atendimento. Esses dados ajudam na identificação do relatório."
      ),
      step(
        '[data-tour="relatorio-periodo"]',
        "#tab-dados-btn",
        "Período",
        "Informe a data inicial e final da viagem ou atendimento. As despesas e trechos devem estar dentro desse período."
      ),
      step(
        '[data-tour="relatorio-adiantamento"]',
        "#tab-dados-btn",
        "Adiantamento",
        "Se houve valor adiantado pelo financeiro, informe aqui. Esse valor será considerado no saldo final."
      ),
      step(
        '[data-tour="relatorio-clientes"]',
        "#tab-dados-btn",
        "Seleção de clientes",
        "Use esta área para selecionar um ou mais clientes vinculados ao relatório."
      ),
      step(
        '[data-tour="relatorio-motivo-clientes"]',
        "#tab-dados-btn",
        "Motivo por cliente",
        "Informe o motivo da viagem para cada cliente selecionado. Esse campo é obrigatório para envio."
      ),
      step(
        '[data-tour="relatorio-tecnicos"]',
        "#tab-dados-btn",
        "Técnicos envolvidos",
        "Informe o técnico responsável e, se houver, os demais técnicos que participaram do atendimento."
      ),
      step(
        '[data-tour="despesas-adicionar"]',
        "#tab-despesas-btn",
        "Adicionar despesa",
        "Clique aqui para incluir uma nova despesa no relatório."
      ),
      step(
        '[data-tour="despesas-linhas"]',
        "#tab-despesas-btn",
        "Linhas de despesa",
        "Cada linha representa uma despesa realizada durante a viagem ou atendimento."
      ),
      step(
        '[data-tour="despesas-data-tipo"]',
        "#tab-despesas-btn",
        "Data e tipo",
        "Informe a data da despesa e selecione o tipo correspondente, como alimentação, hospedagem ou pedágio."
      ),
      step(
        '[data-tour="despesas-descricao-valor"]',
        "#tab-despesas-btn",
        "Descrição e valor",
        "Descreva a despesa de forma clara e informe o valor total do comprovante."
      ),
      step(
        '[data-tour="despesas-clientes"]',
        "#tab-despesas-btn",
        "Clientes da despesa",
        "Selecione os clientes relacionados à despesa. O sistema usará essa informação para rateio."
      ),
      step(
        '[data-tour="despesas-comprovante"]',
        "#tab-despesas-btn",
        "Comprovante",
        "Anexe o comprovante da despesa em PDF, JPG, JPEG ou PNG. O comprovante poderá ser visualizado dentro do sistema."
      ),
      step(
        '[data-tour="km-adicionar"]',
        "#tab-km-btn",
        "Adicionar trecho",
        "Clique aqui para adicionar um trecho de deslocamento."
      ),
      step(
        '[data-tour="km-origem-destino"]',
        "#tab-km-btn",
        "Origem e destino",
        "Informe a origem e o destino do deslocamento. Quando possível, o sistema calcula a rota automaticamente."
      ),
      step(
        '[data-tour="km-mapa"]',
        "#tab-km-btn",
        "Mapa dos deslocamentos",
        "O mapa exibe os trechos informados e ajuda a conferir visualmente o roteiro."
      ),
      step(
        '[data-tour="km-clientes"]',
        "#tab-km-btn",
        "Clientes do trecho",
        "Selecione os clientes relacionados ao deslocamento. O sistema calcula o KM conforme os clientes envolvidos."
      ),
      step(
        '[data-tour="km-divergencia"]',
        "#tab-km-btn",
        "Divergência de KM",
        "Se o KM informado for muito diferente do KM calculado, o sistema poderá alertar o financeiro para conferência."
      ),
      step(
        '[data-tour="km-excedente"]',
        "#tab-km-btn",
        "KM excedente interno",
        "Use este campo para deslocamentos internos, como hotel, cliente, restaurante, evento ou outros trajetos locais."
      ),
      step(
        '[data-tour="resumo-cards"]',
        "#tab-resumo-btn",
        "Resumo financeiro",
        "Aqui você confere os totais do relatório antes de enviar para conferência."
      ),
      step(
        '[data-tour="resumo-alertas"]',
        "#tab-resumo-btn",
        "Alertas e validações",
        "Caso existam pendências ou inconsistências, o sistema exibirá avisos para correção."
      ),
      step(
        '[data-tour="resumo-salvar"]',
        "#tab-resumo-btn",
        "Salvar rascunho",
        "Use esta opção para salvar o relatório sem enviar ao financeiro."
      ),
      step(
        '[data-tour="resumo-enviar"]',
        "#tab-resumo-btn",
        "Enviar para conferência",
        "Quando tudo estiver correto, envie o relatório para análise do setor financeiro."
      ),
      step(
        "#relatorio-form-tour-start",
        "#tab-resumo-btn",
        "Pronto!",
        "Você pode refazer este guia a qualquer momento clicando em Ver guia.",
        "bottom"
      ),
    ];

    return candidates.filter((item) => find(item.element));
  }

  function markSeen() {
    try {
      window.localStorage.setItem(STORAGE_KEY, "true");
    } catch (error) {
      // Sem localStorage disponível, apenas não persiste o estado do tour.
    }
  }

  function hasSeen() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === "true";
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
      closeBtnText: "Pular",
      popoverClass: "relatorio-form-driver-popover",
      steps,
      onHighlightStarted: function (_element, activeStep) {
        activateTab(activeStep.tabSelector);
        scrollToElement(activeStep.element);
      },
      onDestroyed: markSeen,
    });

    if (force || !hasSeen()) {
      driverObj.drive();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("relatorio-form-tour-start");
    if (button) {
      button.addEventListener("click", function () {
        startTour(true);
      });
    }

    window.setTimeout(function () {
      startTour(false);
    }, 700);
  });
})();
