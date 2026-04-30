console.log("JS carregou");
// ── DATA NO NAVBAR ──
const el = document.getElementById('current-date');
if (el) {
  const now = new Date();
  el.textContent = now.toLocaleDateString('pt-BR', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric'
  });
}

// ── TOGGLE TEMA CLARO/ESCURO ──
const themeToggle = document.getElementById('theme-toggle');
const themeIcon = document.getElementById('theme-icon');
const html = document.documentElement;

const savedTheme = localStorage.getItem('theme') || 'light';
html.setAttribute('data-bs-theme', savedTheme);
themeIcon.className = savedTheme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';

if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const current = html.getAttribute('data-bs-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    themeIcon.className = next === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
  });
}

//Modal de confirmação de envio sem comprovante
document.addEventListener("DOMContentLoaded", function () {

  const form = document.getElementById("form-relatorio");
  const btnEnviar = document.getElementById("btn-enviar-relatorio");
  const confirmar = document.getElementById("confirmar-envio-sem-comprovante");
  const modal = new bootstrap.Modal(
    document.getElementById("modalSemComprovante")
  );

  if (!form || !btnEnviar) return;


  function faltamComprovantes() {

    let faltam = false;

    document.querySelectorAll(".linha-despesa").forEach(linha => {

      const descricao =
        linha.querySelector('[name$="-descricao"]');

      if (!descricao?.value?.trim()) return;

      const file =
        linha.querySelector('input[type=file]');

      const anexoExistente =
        linha.querySelector('a[href]');

      if (file && !file.value && !anexoExistente) {
        faltam = true;
      }

    });

    return faltam;

  }



  form.addEventListener("submit", function (e) {

    if (form.dataset.confirmado === "1") {
      return;
    }

    if (faltamComprovantes()) {
      e.preventDefault();
      modal.show();
    }

  });


  confirmar.addEventListener("click", function () {

    form.dataset.confirmado = "1";

    let acao = form.querySelector('[name="acao"]');

    if (!acao) {
      acao = document.createElement("input");
      acao.type = "hidden";
      acao.name = "acao";
      acao.value = "enviar";
      form.appendChild(acao);
    }

    modal.hide();

    form.submit();

  });

});

const FormState = (() => {
  // Map<HTMLElement (campo), { touched: bool, dirty: bool }>
  const _state = new WeakMap();

  function get(campo) {
    if (!_state.has(campo)) _state.set(campo, { touched: false, dirty: false });
    return _state.get(campo);
  }

  function touch(campo) { get(campo).touched = true; }
  function dirty(campo) { get(campo).dirty = true; }
  function isTouched(campo) { return get(campo).touched; }

  return { touch, dirty, isTouched };
})();


/* ============================================================
   2. UI — manipulação de DOM isolada por tipo de mensagem
   Cada tipo tem um seletor CSS próprio e nunca interfere
   nos outros.
============================================================ */
const UI = (() => {

  /**
   * Encontra o container de mensagem dentro do mesmo .form-group / td
   * do campo, sem depender de parentElement frágil.
   * Sobe até encontrar a célula ou o wrapper mais próximo que contenha
   * o seletor desejado.
   */
  function _container(campo, seletor) {
    // Sobe até .form-group, td, ou a própria linha — máx 4 níveis
    let el = campo.parentElement;
    for (let i = 0; i < 4; i++) {
      if (!el) break;
      const found = el.querySelector(seletor);
      if (found) return found;
      el = el.parentElement;
    }
    return null;
  }

  function setError(campo, msg) {
    campo.classList.add("is-invalid");
    campo.classList.remove("is-valid");
    const el = _container(campo, ".erro-inline");
    if (el) el.textContent = msg;
  }

  function clearError(campo) {
    campo.classList.remove("is-invalid");
    const el = _container(campo, ".erro-inline");
    if (el) el.textContent = "";
  }

  function setWarning(campo, msg, tipo = "geral") {
    const seletor = {
      geral: ".aviso-inline",
      refeicao: ".aviso-refeicao",
      deslocamento: ".aviso-deslocamento",
      duplicata: ".aviso-duplicata",
    }[tipo] || ".aviso-inline";

    const el = _container(campo, seletor);
    if (el) el.textContent = msg;
  }

  function clearWarning(campo, tipo = "geral") {
    const seletor = {
      geral: ".aviso-inline",
      refeicao: ".aviso-refeicao",
      deslocamento: ".aviso-deslocamento",
      duplicata: ".aviso-duplicata",
    }[tipo] || ".aviso-inline";

    const el = _container(campo, seletor);
    if (el) el.textContent = "";
  }

  /** Limpa TODOS os tipos de aviso de um campo de uma vez */
  function clearAllWarnings(campo) {
    ["geral", "refeicao", "deslocamento", "duplicata"].forEach(tipo =>
      clearWarning(campo, tipo)
    );
  }

  /** Limpa um seletor específico dentro de uma linha (para validações de grupo) */
  function clearGroupWarning(linha, seletor) {
    const el = linha.querySelector(seletor);
    if (el) el.textContent = "";
  }

  function setGroupWarning(linha, seletor, msg) {
    const el = linha.querySelector(seletor);
    if (el) el.textContent = msg;
  }

  return {
    setError, clearError, setWarning, clearWarning, clearAllWarnings,
    clearGroupWarning, setGroupWarning
  };
})();


/* ============================================================
   3. HELPERS
============================================================ */

/** Retorna o período do relatório a partir do form */
function getPeriodo() {
  const inicio = document.querySelector('[name="data_inicio"]')?.value || null;
  const fim = document.querySelector('[name="data_fim"]')?.value || null;

  return { inicio, fim };
}

/** Verifica se uma linha está "removida" (opacity 0.3 ou display:none) */
function linhaRemovida(linha) {
  return (
    linha.style.opacity === "0.3" ||
    linha.style.display === "none" ||
    linha.classList.contains("linha-removida")
  );
}

/** Retorna linhas ativas de um seletor */
function linhasAtivas(selector) {
  return Array.from(document.querySelectorAll(selector))
    .filter(l => l.style.display !== "none");
}


/* ============================================================
   4. VALIDADORES INDIVIDUAIS
   Cada um recebe o campo (ou linha) e retorna
   { ok: bool, msg: string } — sem tocar no DOM.
============================================================ */
const Validators = {

  dataFora(campo) {
    const { inicio, fim } = getPeriodo();
    const v = campo.value;

    // Guarda: só valida se o campo tiver valor
    if (!v) return { ok: true };

    // Regex aceita apenas YYYY-MM-DD — rejeita "", "None", undefined, null
    const reData = /^\d{4}-\d{2}-\d{2}$/;
    const periodoValido = reData.test(inicio) && reData.test(fim);

    // Se período ainda não foi definido corretamente, não exibe erro
    if (!periodoValido) return { ok: true };

    // Comparação de string é segura para YYYY-MM-DD (formato ISO, lexicograficamente ordenado)
    if (v < inicio || v > fim) {
      return { ok: false, msg: "Data fora do período do relatório." };
    }

    return { ok: true };
  },

  tipoObrigatorio(campo) {
    if (!campo.value?.trim()) {
      return { ok: false, msg: "Selecione o tipo." };
    }
    return { ok: true };
  },

  valorObrigatorio(campo) {
    // Só valida após "touched"
    if (!FormState.isTouched(campo)) return { ok: true };
    const v = campo.value?.trim();
    if (!v) return { ok: false, msg: "Informe o valor." };
    if (parseFloat(v) <= 0) return { ok: false, msg: "Valor deve ser maior que zero." };
    return { ok: true };
  },

  origemDestinoObrigatorio(campo) {
    if (!campo.value?.trim()) {
      return { ok: false, msg: "Campo obrigatório." };
    }
    return { ok: true };
  },

  // --- Avisos individuais ---

  descricaoCurta(campo) {
    const v = campo.value?.trim();
    if (v && v.length < 4) {
      return { ok: false, msg: "Descrição pouco detalhada." };
    }
    return { ok: true };
  },

  valorAltoSemObs(campoValor, linhaDesp) {
    const valor = parseFloat(campoValor.value) || 0;
    if (valor <= 300) return { ok: true };

    const obs = linhaDesp.querySelector(
      'input[name$="-observacoes"], textarea[name$="-observacoes"]'
    );
    if (!obs?.value?.trim()) {
      return { ok: false, msg: "Recomenda-se detalhar despesas altas em Observações." };
    }
    return { ok: true };
  },
};


/* ============================================================
   5. VALIDADORES DE GRUPO
   Não recebem campo — operam sobre todas as linhas ativas.
   Limpam avisos antigos antes de reaplicar.
============================================================ */
const GroupValidators = {

  /**
   * 4+ despesas de alimentação no mesmo dia
   * → aviso apenas na última linha do grupo
   */
  multiplasRefeicoes() {
    const linhas = linhasAtivas(".linha-despesa");

    // 🔥 limpa tudo primeiro (só linhas ativas)
    linhas.forEach(linha =>
      UI.clearGroupWarning(linha, ".aviso-refeicao")
    );

    const mapa = {};

    linhas.forEach(linha => {
      const data = linha.querySelector('input[name$="-data"]')?.value;
      const tipo = linha.querySelector('select[name$="-tipo"]')?.value;

      if (!data) return;

      const tipoNormalizado = (tipo || "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "");

      if (tipoNormalizado !== "alimentacao") return;

      (mapa[data] = mapa[data] || []).push(linha);
    });

    Object.values(mapa).forEach(grupo => {
      if (grupo.length < 4) return;

      // 🔥 SEMPRE pega a última linha atual (já recalculado)
      const ultima = grupo[grupo.length - 1];

      UI.setGroupWarning(
        ultima,
        ".aviso-refeicao",
        `Muitas refeições nesta data (${grupo.length}).`
      );
    });
  },

  /**
   * Despesas duplicadas: mesmo (data + tipo + valor)
   * → aviso em todas as linhas duplicadas (exceto a primeira)
   */
  despesasDuplicadas() {
    const linhas = linhasAtivas(".linha-despesa");

    // 🔥 limpa tudo antes
    linhas.forEach(linha =>
      UI.clearGroupWarning(linha, ".aviso-duplicata")
    );

    const mapa = {};

    linhas.forEach(linha => {
      const data = linha.querySelector('input[name$="-data"]')?.value;
      const tipo = linha.querySelector('select[name$="-tipo"]')?.value;
      const valor = linha.querySelector('input[name$="-valor"]')?.value?.trim();

      if (!data || !tipo || !valor) return;

      const chave = `${data}|${tipo}|${valor}`;
      (mapa[chave] = mapa[chave] || []).push(linha);
    });

    Object.values(mapa).forEach(grupo => {
      if (grupo.length < 2) return;

      // 🔥 recalcula SEMPRE com base no estado atual
      grupo.slice(1).forEach(linha => {
        UI.setGroupWarning(
          linha,
          ".aviso-duplicata",
          "Possível despesa duplicada (mesma data, tipo e valor)."
        );
      });
    });
  },

  /**
   * 5+ deslocamentos no mesmo dia
   * → aviso apenas na última linha do grupo
   */
  muitosDeslocamentos() {
    const linhas = document.querySelectorAll(".linha-trecho");

    // Limpa todos
    linhas.forEach(linha =>
      UI.clearGroupWarning(linha, ".aviso-deslocamento")
    );

    const mapa = {};

    linhasAtivas(".linha-trecho").forEach(linha => {
      const data = linha.querySelector('input[name$="-data"]')?.value;
      if (!data) return;
      (mapa[data] = mapa[data] || []).push(linha);
    });

    Object.values(mapa).forEach(grupo => {
      if (grupo.length < 5) return;
      const ultima = grupo[grupo.length - 1];
      UI.setGroupWarning(ultima, ".aviso-deslocamento",
        `Muitos deslocamentos nesta data (${grupo.length}).`
      );
    });
  },

  /** Executa todos os validadores de grupo */
  runAll() {
    this.multiplasRefeicoes();
    this.despesasDuplicadas();
    this.muitosDeslocamentos();
  },
};


/* ============================================================
   6. REGRAS POR CAMPO
   Define quais validadores rodar para cada campo,
   sem misturar regras em uma função monolítica.
 
   Cada regra é { test: fn → bool, onFail: fn, onPass: fn }
   "test" retorna true se a regra se aplica ao campo recebido.
============================================================ */
const FieldRules = [

  // --- ERROS ---

  {
    // Data fora do período (despesas e trechos)
    test: c => c.type === "date" && !!c.value,
    run(campo) {
      const r = Validators.dataFora(campo);
      r.ok ? UI.clearError(campo) : UI.setError(campo, r.msg);
    },
  },

  {
    // Tipo obrigatório (select de despesas)
    test: c => c.tagName === "SELECT" && c.name?.includes("-tipo"),
    run(campo) {
      const r = Validators.tipoObrigatorio(campo);
      r.ok ? UI.clearError(campo) : UI.setError(campo, r.msg);
    },
  },

  {
    // Valor obrigatório (após touched)
    test: c => c.classList.contains("campo-valor-desp"),
    run(campo) {
      const r = Validators.valorObrigatorio(campo);
      r.ok ? UI.clearError(campo) : UI.setError(campo, r.msg);
    },
  },

  {
    // Origem / Destino obrigatórios
    test: c => c.name?.includes("-origem") || c.name?.includes("-destino"),
    run(campo) {
      const r = Validators.origemDestinoObrigatorio(campo);
      r.ok ? UI.clearError(campo) : UI.setError(campo, r.msg);
    },
  },

  // --- AVISOS ---

  {
    // Descrição curta
    test: c => c.name?.includes("-descricao"),
    run(campo) {
      const r = Validators.descricaoCurta(campo);
      r.ok
        ? UI.clearWarning(campo, "geral")
        : UI.setWarning(campo, r.msg, "geral");
    },
  },

  {
    // Valor alto sem observação
    test: c => c.classList.contains("campo-valor-desp"),
    run(campo) {
      const linhaDesp = campo.closest(".linha-despesa");
      if (!linhaDesp) return;
      const r = Validators.valorAltoSemObs(campo, linhaDesp);
      r.ok
        ? UI.clearWarning(campo, "geral")
        : UI.setWarning(campo, r.msg, "geral");
    },
  },
  {
    test: c => c.name?.includes("-km") || c.name?.includes("-valor_km"),
    run(campo) {
      const linha = campo.closest(".linha-trecho");
      if (!linha) return;

      const campoKm = linha.querySelector('input[name$="-km"]');
      const campoVkm = linha.querySelector('input[name$="-valor_km"]');

      const km = parseFloat(campoKm?.value);
      const vkm = parseFloat(campoVkm?.value);

      // limpa erros primeiro
      UI.clearError(campoKm);
      UI.clearError(campoVkm);

      // regra: km obrigatório se qualquer campo preenchido
      if ((campoKm?.value || campoVkm?.value)) {

        if (!campoKm?.value) {
          UI.setError(campoKm, "Informe o KM.");
        } else if (km < 0.1) {
          UI.setError(campoKm, "KM deve ser no mínimo 0.1.");
        }

        if (!campoVkm?.value) {
          UI.setError(campoVkm, "Informe o valor por KM.");
        }
      }
    }
  },
  {
    test: c => c.name?.includes("-valor_km"),

    run(campo) {
      const valor = parseFloat(campo.value) || 0;
      const padrao = getValorKmPadrao();

      // limpa antes
      UI.clearWarning(campo);

      if (!valor || !padrao) return;

      if (valor !== padrao) {
        UI.setWarning(
          campo,
          `Valor diferente do padrão (${padrao.toFixed(2)}).`
        );
      }
    }
  }
];


/* ============================================================
   7. BADGES DAS ABAS
============================================================ */
function atualizarBadgesAbas() {
  const badgeDesp = document.getElementById("badge-despesas");
  const badgeKm = document.getElementById("badge-km");

  if (badgeDesp) {
    const erros = document.querySelectorAll("#corpo-despesas .is-invalid").length;
    const total = document.querySelectorAll("#corpo-despesas .linha-despesa").length;
    badgeDesp.className = erros > 0
      ? "badge bg-danger rounded-pill ms-1"
      : "badge bg-primary rounded-pill ms-1";
    badgeDesp.textContent = erros > 0 ? "!" : total;
  }

  if (badgeKm) {
    const erros = document.querySelectorAll("#corpo-trechos .is-invalid").length;
    const total = document.querySelectorAll("#corpo-trechos .linha-trecho").length;
    badgeKm.className = erros > 0
      ? "badge bg-danger rounded-pill ms-1"
      : "badge bg-secondary rounded-pill ms-1";
    badgeKm.textContent = erros > 0 ? "!" : total;
  }
}


/* ============================================================
   8. CONTROLLER — ponto de entrada e gestão de eventos
============================================================ */
const Controller = (() => {

  /**
   * Aplica todas as FieldRules que se aplicam ao campo.
   * Não chama GroupValidators — isso é feito separadamente
   * para controlar a ordem de execução.
   */
  function validarCampo(campo) {
    if (!campo?.name) return;

    // Ignora campos dentro de linhas removidas
    const linha = campo.closest(".linha-despesa, .linha-trecho");
    if (linha && linhaRemovida(linha)) return;

    FieldRules.forEach(regra => {
      if (regra.test(campo)) regra.run(campo);
    });
  }

  /**
   * Debounce simples: agrupa chamadas rápidas em uma só.
   * Evita reprocessar GroupValidators a cada tecla.
   */
  let _timer = null;
  function scheduleGroupValidation() {
    clearTimeout(_timer);
    _timer = setTimeout(() => {
      GroupValidators.runAll();
      atualizarBadgesAbas();
    }, 150);
  }
  function init() {
    const form = document.getElementById("form-relatorio");
    if (!form) return;

    // input → valida campo + agenda validações de grupo
    form.addEventListener("input", e => {
      FormState.dirty(e.target);
      validarCampo(e.target);
      scheduleGroupValidation();
    });

    // change → valida campo + trata mudanças de período
    form.addEventListener("change", e => {
      FormState.dirty(e.target);
      validarCampo(e.target);

      // 🔥 NOVO: se alterar período, revalida TODAS as despesas
      if (e.target.name === "data_inicio" || e.target.name === "data_fim") {

        const datasDespesas = document.querySelectorAll(
          ".linha-despesa input[type='date']"
        );

        datasDespesas.forEach(campo => {
          validarCampo(campo);
        });
      }

      scheduleGroupValidation();
    });

    // blur → trata campos "touched" e datas
    form.addEventListener("blur", e => {

      if (e.target.name?.includes("-valor")) {
        FormState.touch(e.target);
        validarCampo(e.target);
        scheduleGroupValidation();
      }

      if (e.target.type === "date") {
        validarCampo(e.target);
        scheduleGroupValidation();
      }

    }
      , true); // capture = pega antes do stop

    form.addEventListener("submit", function (e) {
      // força validação completa
      GroupValidators.runAll();

      const temErro = document.querySelectorAll(".is-invalid").length > 0;

      if (temErro) {
        e.preventDefault();

        alert("Existem erros no formulário. Corrija antes de enviar.");

        // opcional: focar primeiro erro
        const primeiroErro = document.querySelector(".is-invalid");
        if (primeiroErro) primeiroErro.focus();
      }
    });
    // Validação inicial (edição de relatório)
    _validarLinhasIniciais();

    atualizarBadgesAbas();
  }

  /**
   * Valida todos os campos já presentes ao carregar a página.
   * Não marca campos "não tocados" com erro — apenas detecta
   * estados já inválidos (ex: datas fora do período).
   */
  function _validarLinhasIniciais() {
    document.querySelectorAll(
      ".linha-despesa input, .linha-despesa select, " +
      ".linha-trecho input, .linha-trecho select"
    ).forEach(campo => {
      if (campo.value) validarCampo(campo);
    });
    GroupValidators.runAll();
  }

  /**
   * Chamado pelo código que adiciona linhas dinamicamente.
   * Exemplo: Controller.onLinhaAdicionada(novaLinha)
   */
  function onLinhaAdicionada(linha) {
    // Nenhuma validação de erro imediata — espera o usuário interagir.
    // Apenas atualiza badges e grupos.
    atualizarBadgesAbas();
    scheduleGroupValidation();
  }

  /**
   * Chamado quando uma linha é marcada como removida.
   */
  function onLinhaRemovida(_linha) {
    atualizarBadgesAbas();
    scheduleGroupValidation();
  }

  return { init, validarCampo, onLinhaAdicionada, onLinhaRemovida };
})();

function validarValorKm(campo) {
  const linha = campo.closest(".linha-trecho");
  if (!linha) return;

  const form = document.getElementById("form-relatorio");
  const valorPadrao = parseFloat(form?.dataset.valorKmCliente);

  const valor = parseFloat(campo.value);
  const aviso = linha.querySelector(".aviso-km");

  if (!valor || !valorPadrao) {
    if (aviso) aviso.textContent = "";
    return;
  }

  if (valor !== valorPadrao) {
    if (aviso) {
      aviso.textContent = `Valor diferente do padrão (R$ ${valorPadrao.toFixed(2)})`;
    }
  } else {
    if (aviso) aviso.textContent = "";
  }
}

function getValorKmPadrao() {
  const form = document.getElementById("form-relatorio");
  return parseFloat(form?.dataset.valorKmCliente) || 0;
}

function preencherValorKmPadrao(linha) {
  const campo = linha.querySelector('input[name$="-valor_km"]');
  if (!campo || campo.value) return;

  const form = document.getElementById("form-relatorio");
  const valor = parseFloat(form?.dataset.valorKmCliente);

  if (valor) {
    campo.value = valor.toFixed(4);
  }
}
/* ============================================================
   9. INICIALIZAÇÃO
============================================================ */
document.addEventListener("DOMContentLoaded", () => Controller.init());