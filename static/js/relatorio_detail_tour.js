(function () {
  const STORAGE_KEY = "relatorioDetailTourVisto:v1";

  function getDriverFactory() {
    return window.driver && window.driver.js && window.driver.js.driver;
  }

  function element(selector) {
    return document.querySelector(selector);
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

  function abrirCollapseSeNecessario(target) {
    if (!target) return;

    const collapse = target.closest(".collapse:not(.show)");
    if (!collapse) return;

    if (window.bootstrap && window.bootstrap.Collapse) {
      window.bootstrap.Collapse.getOrCreateInstance(collapse, { toggle: false }).show();
    } else {
      collapse.classList.add("show");
    }
  }

  function garantirElementoVisivel(selector) {
    const target = element(selector);
    if (!target) return;

    abrirCollapseSeNecessario(target);

    window.setTimeout(function () {
      target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    }, 120);
  }

  function step(selector, title, description, side, align) {
    return {
      element: selector,
      popover: {
        title,
        description,
        side: side || "bottom",
        align: align || "center",
      },
    };
  }

  function montarEtapasRelatorioDetail() {
    const candidates = [
      step(
        '[data-tour="detail-cabecalho"]',
        "Análise financeira do relatório",
        "Esta tela é usada para conferir despesas, KM, comprovantes, rateios e valores antes da aprovação ou rejeição do relatório.",
        "bottom",
        "start"
      ),
      step(
        '[data-tour="detail-cabecalho"]',
        "Identificação do relatório",
        "Aqui ficam as informações principais, como número, cliente(s), técnico(s), período e status atual do relatório.",
        "bottom",
        "start"
      ),
      step(
        '[data-tour="detail-status"]',
        "Status do relatório",
        "O status indica em qual etapa o relatório está, como conferência pendente, ajuste pendente, aprovado ou rejeitado.",
        "bottom",
        "center"
      ),
      step(
        '[data-tour="detail-resumo-global"]',
        "Resumo financeiro global",
        "Este bloco mostra os totais gerais do relatório, incluindo despesas, KM, total solicitado, total aprovado, diferença removida, adiantamento e saldo final.",
        "left",
        "start"
      ),
      step(
        '[data-tour="detail-atencoes"]',
        "Atenções identificadas",
        "O sistema mostra alertas para apoiar a conferência, como falta de comprovante, descrição curta, despesa alta ou divergência de KM.",
        "bottom",
        "start"
      ),
      step(
        '[data-tour="detail-distribuicao-cliente"]',
        "Distribuição por cliente",
        "Quando o relatório possui múltiplos clientes, este card mostra o resumo financeiro individual de cada cliente, incluindo motivo da viagem, despesas, KM, total aprovado e diferenças.",
        "left",
        "start"
      ),
      step(
        '[data-tour="detail-despesas"]',
        "Despesas lançadas",
        "Aqui são exibidas as despesas informadas pelo técnico. A linha principal mostra a despesa original e os detalhes mostram o rateio por cliente.",
        "top",
        "start"
      ),
      step(
        '[data-tour="detail-despesa-mais-info"]',
        "Mais informações",
        "Use esta opção para expandir dados complementares da despesa, como observações, cliente(s), comprovantes e rateio.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-despesa-comprovante"]',
        "Comprovante da despesa",
        "Clique no ícone para visualizar o comprovante dentro do sistema. Também é possível abrir em nova aba ou baixar o arquivo.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-despesa-rateio"]',
        "Rateio da despesa",
        "Este recurso mostra como o valor da despesa foi distribuído entre os clientes. Quando permitido, o financeiro pode ajustar o rateio informando justificativa.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-despesa-valor-aprovado"]',
        "Valor aprovado",
        "O financeiro pode aprovar um valor diferente do solicitado. Se o campo ficar vazio, o sistema considera o valor solicitado como aprovado.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-despesa-remover"]',
        "Remover do reembolso",
        "Use esta ação para rejeitar uma despesa individualmente. O item continua visível para auditoria, mas deixa de compor o valor aprovado.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-km"]',
        "Trechos de KM",
        "Aqui ficam os deslocamentos informados no relatório, com origem, destino, KM informado, KM calculado, clientes vinculados e valores aprovados.",
        "top",
        "start"
      ),
      step(
        '[data-tour="detail-km-mapa"]',
        "Mapa dos deslocamentos",
        "O mapa exibe visualmente os trechos do relatório. Ele ajuda a conferir origem, destino, sequência dos deslocamentos e possíveis divergências.",
        "bottom",
        "start"
      ),
      step(
        '[data-tour="detail-km-divergencia"]',
        "Divergência de KM",
        "Quando o KM informado difere significativamente do KM calculado pela rota, o sistema destaca o trecho para conferência do financeiro.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-km-rateio"]',
        "Cálculo de KM por cliente",
        "No KM multi-cliente, o sistema calcula o valor de cada cliente considerando o KM informado e o valor por KM configurado para cada cliente.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-km-valor-aprovado"]',
        "Valor aprovado do KM",
        "O financeiro pode conferir os detalhes de aprovação e rateio do KM mantendo a rastreabilidade financeira.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-km-remover"]',
        "Remover KM do reembolso",
        "Use esta ação para rejeitar um trecho de KM. O trecho continuará visível, mas não será considerado no valor aprovado.",
        "left",
        "center"
      ),
      step(
        '[data-tour="detail-km-excedente"]',
        "KM excedente interno",
        "Este bloco mostra deslocamentos internos, como hotel para cliente, cliente para restaurante ou evento para hotel. Ele é discriminado separadamente dos trechos principais.",
        "top",
        "start"
      ),
      step(
        '[data-tour="detail-solicitar-ajuste"]',
        "Solicitar ajuste",
        "Use esta ação para devolver o relatório ao técnico com uma justificativa obrigatória, quando houver informações que precisam ser corrigidas.",
        "bottom",
        "center"
      ),
      step(
        '[data-tour="detail-rejeitar-relatorio"]',
        "Rejeitar relatório",
        "Use esta ação somente quando o relatório não puder ser aprovado nem corrigido. A justificativa é obrigatória e a rejeição é definitiva.",
        "bottom",
        "center"
      ),
      step(
        '[data-tour="detail-aprovar-relatorio"]',
        "Aprovar relatório",
        "Após conferir despesas, KM, comprovantes, rateios e valores, use esta ação para finalizar o relatório. Depois da aprovação, os dados ficam congelados e o relatório passa a ser somente consulta.",
        "bottom",
        "center"
      ),
      step(
        '[data-tour="detail-historico"]',
        "Histórico do relatório",
        "O histórico registra eventos importantes, como envio, ajustes, rejeições de itens, alterações de valores, aprovação e geração de documentos.",
        "left",
        "start"
      ),
      step(
        '[data-tour="detail-pdfs"]',
        "Documentos PDF",
        "Nesta área ficam os documentos gerados, como PDF interno, PDF do cliente e pacote ZIP com PDFs por cliente, conforme disponível.",
        "bottom",
        "center"
      ),
      step(
        "#relatorio-detail-tour-start",
        "Pronto!",
        "Você pode refazer este guia a qualquer momento clicando em Ver guia.",
        "bottom",
        "center"
      ),
    ];

    return candidates.filter((item) => item.element && element(item.element));
  }

  function iniciarTourRelatorioDetail(force) {
    const driverFactory = getDriverFactory();
    if (!driverFactory) return;

    const steps = montarEtapasRelatorioDetail();
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
      popoverClass: "relatorio-detail-driver-popover",
      steps,
      onHighlightStarted: function (_element, currentStep) {
        if (currentStep && currentStep.element) {
          garantirElementoVisivel(currentStep.element);
        }
      },
      onDestroyed: markSeen,
    });

    if (force || !hasSeen()) {
      driverObj.drive();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const button = document.getElementById("relatorio-detail-tour-start");
    if (button) {
      button.addEventListener("click", function () {
        iniciarTourRelatorioDetail(true);
      });
    }

    window.setTimeout(function () {
      iniciarTourRelatorioDetail(false);
    }, 650);
  });
})();
