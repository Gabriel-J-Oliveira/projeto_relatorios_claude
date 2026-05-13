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
if (themeIcon) {
  themeIcon.className = savedTheme === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
}

if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    const current = html.getAttribute('data-bs-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    if (themeIcon) {
      themeIcon.className = next === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
    }
  });
}

//Modal de confirmação de envio sem comprovante
document.addEventListener("DOMContentLoaded", function () {

  const form = document.getElementById("form-relatorio");
  const btnEnviar = document.getElementById("btn-enviar-relatorio");
  const confirmar = document.getElementById("confirmar-envio-sem-comprovante");
  const modalEl = document.getElementById("modalSemComprovante");

  if (!form || !btnEnviar || !confirmar || !modalEl) return;

  const modal = new bootstrap.Modal(modalEl);


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

  let ultimaAcao = null;

  document.getElementById("btn-enviar-rascunho")?.addEventListener("click", () => {
    ultimaAcao = "rascunho";
  });

  document.getElementById("btn-enviar-relatorio")?.addEventListener("click", () => {
    ultimaAcao = "enviar";
  });

  form.addEventListener("submit", function (e) {

    if (form.dataset.confirmado === "1") {
      return;
    }

    // Só valida comprovante se for envio
    if (ultimaAcao === "enviar" && faltamComprovantes()) {
      e.preventDefault();
      modal.show();
    }

  });


  confirmar.addEventListener("click", function () {
    form.dataset.confirmado = "1";

    // Remove qualquer input hidden "acao" anterior para não duplicar
    form.querySelectorAll('input[type="hidden"][name="acao"]').forEach(el => el.remove());

    // Cria um input hidden com value="enviar" — este SIM vai no POST
    const inputAcao = document.createElement("input");
    inputAcao.type = "hidden";
    inputAcao.name = "acao";
    inputAcao.value = "enviar";
    form.appendChild(inputAcao);

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
    // 1. tenta no mesmo TD (mais correto)
    const td = campo.closest("td");
    if (td) {
      const found = td.querySelector(seletor);
      if (found) return found;
    }

    // 2. fallback (casos especiais)
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

document.addEventListener("blur", function (e) {

  // 1. Marca campo como "tocado" (global)
  if (e.target.matches("input, select, textarea")) {
    e.target.dataset.touched = "true";
  }

  // 2. Regra específica: valor_km
  if (e.target.matches('input[name$="-valor_km"]')) {

    if (e.target.value) {
      e.target.dataset.editado = "true";
      validarKmGlobal();
    }

  }

}, true);

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
    .filter(l => !linhaRemovida(l));
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

  // limpa tudo (inclusive removidas)
  document.querySelectorAll(".linha-despesa").forEach(linha =>
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

  // limpa tudo antes
  document.querySelectorAll(".linha-despesa").forEach(linha =>
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
// ADICIONAR antes da definição de FieldRules:
function getValorKmPadrao() {
  const form = document.getElementById("form-relatorio");
  return parseFloat(form?.dataset?.valorKmCliente) || 0;
}

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

      const kmTouched = campoKm?.dataset.touched;
      const vkmTouched = campoVkm?.dataset.touched;

      // limpa antes
      UI.clearError(campoKm);
      UI.clearError(campoVkm);

      // 🔥 só valida depois de interação
      if (kmTouched || vkmTouched) {

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

      const tolerancia = 0.0001;
      const divergente = Math.abs(valor - padrao) > tolerancia;

      if (divergente) {
        UI.setWarning(
          campo,
          `Valor diferente do padrão (${padrao.toFixed(2)}).`
        );
      } else {
        UI.clearWarning(campo);
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

// ADICIONAR antes de Controller.init() ou dentro do objeto Controller:
function _validarLinhasIniciais() {
  document.querySelectorAll(".linha-despesa, .linha-trecho").forEach(linha => {
    linha.querySelectorAll("input, select, textarea").forEach(campo => {
      if (campo.name) Controller.validarCampo?.(campo);
    });
  });
}

/* ============================================================
   8. CONTROLLER — ponto de entrada e gestão de eventos
============================================================ */
const Controller = (() => {

  function validarCampo(campo) {
    if (!campo?.name) return;

    const linha = campo.closest(".linha-despesa, .linha-trecho");
    if (linha && linhaRemovida(linha)) return;

    FieldRules.forEach(regra => {
      if (regra.test(campo)) regra.run(campo);
    });
  }

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

    form.addEventListener("input", e => {
      FormState.dirty(e.target);
      validarCampo(e.target);
      scheduleGroupValidation();
    });

    form.addEventListener("change", e => {
      FormState.dirty(e.target);
      validarCampo(e.target);

      if (e.target.name === "data_inicio" || e.target.name === "data_fim") {
        document.querySelectorAll(".linha-despesa input[type='date']")
          .forEach(campo => validarCampo(campo));
      }

      scheduleGroupValidation();
    });

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
    }, true);

    form.addEventListener("submit", function (e) {
      GroupValidators.runAll();

      const temErro = document.querySelectorAll(".is-invalid").length > 0;

      if (temErro) {
        e.preventDefault();
        alert("Existem erros no formulário.");

        const primeiroErro = document.querySelector(".is-invalid");
        if (primeiroErro) primeiroErro.focus();
      }
    });

    atualizarBadgesAbas();
    validarKmGlobal();
  }

  // 🔥 ESSA PARTE FALTAVA
  return {
    init,
    validarCampo
  };

})();
/**
 * Valida todos os campos já presentes ao carregar a página.
 * Não marca campos "não tocados" com erro — apenas detecta
 * estados já inválidos (ex: datas fora do período).
 */
const campoCliente = document.querySelector('[name="cliente"]');

campoCliente?.addEventListener("change", function () {
  const form = document.getElementById("form-relatorio");

  // 1. pega valor antigo ANTES de qualquer alteração
  const valorAntigo = parseFloat(form?.dataset.valorKmCliente);

  const opt = this.selectedOptions?.[0];
  const novoValor = parseFloat(opt?.dataset.valorKm || "");

  if (!Number.isFinite(novoValor) || novoValor <= 0) return;

  document.querySelectorAll(".linha-trecho").forEach(linha => {
    const campo = linha.querySelector('input[name$="-valor_km"]');
    if (!campo) return;

    const foiEditado = campo.dataset.editado === "true";

    // ✔️ regra correta
    if (!campo.value || !foiEditado) {
      campo.value = novoValor.toFixed(2);

      const campoKm = linha.querySelector('input[name$="-km"]');
      if (campoKm) calcularTrechoKm(campoKm);
    }
  });

  // 🔵 2. só agora atualiza o padrão global
  atualizarValorKmPadraoAtual(novoValor);

  recalcular();
  validarKmGlobal();
});

/* ============================================================
   9. INICIALIZAÇÃO
============================================================ */
document.addEventListener("DOMContentLoaded", () => Controller.init());
