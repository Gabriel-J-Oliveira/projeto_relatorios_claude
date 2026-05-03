import logging
import datetime
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from .models import (
    Adiantamento,
    Cliente,
    ItemDespesa,
    PoliticaValor,
    RelatorioTecnico,
    StatusRelatorio,
    Tecnico,
    TrechoKm,
)
from .forms import (
    AdiantamentoForm,
    ClienteForm,
    ItemDespesaForm,
    ItemDespesaFormSet,
    RelatorioFiltroForm,
    RelatorioTecnicoForm,
    TecnicoForm,
    TrechoKmForm,
    TrechoKmFormSet,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS INTERNOS
# ─────────────────────────────────────────────


def _get_valor_km_para_cliente(cliente_id) -> float:
    """
    Busca o valor_km real do model Cliente.
    Retorna float 0.0 se não encontrado ou inválido.

    IMPORTANTE: o campo real no model é `valor_km`.
    O nome `valor_km_padrao` é APENAS uma variável auxiliar
    usada no frontend e nos kwargs dos forms — nunca no banco.
    """
    if not cliente_id:
        return 0.0
    try:
        valor = (
            Cliente.objects.filter(pk=cliente_id)
            .values_list("valor_km", flat=True)  # campo real do model
            .first()
        )
        return float(valor) if valor else 0.0
    except (TypeError, ValueError):
        logger.warning(
            "_get_valor_km_para_cliente: valor inválido para cliente_id=%s", cliente_id
        )
        return 0.0


def _form_has_content(form) -> bool:
    """Retorna True se o form tem dados reais (não está vazio e não está marcado para DELETE)."""
    if not hasattr(form, "cleaned_data"):
        return False
    if form.cleaned_data.get("DELETE"):
        return False
    values = [
        v
        for k, v in form.cleaned_data.items()
        if k not in {"DELETE", "id", "relatorio"}
    ]
    return any(v not in (None, "", [], ()) for v in values)


def _sync_equipe(relatorio, tecnicos_apoio):
    """Sincroniza técnicos de apoio do relatório (M2M via through)."""
    from .models import RelatorioTecnicoEquipe

    relatorio.equipe.exclude(tecnico__in=tecnicos_apoio).delete()
    existentes = set(relatorio.equipe.values_list("tecnico_id", flat=True))
    for tecnico in tecnicos_apoio:
        if tecnico.pk not in existentes:
            RelatorioTecnicoEquipe.objects.create(
                relatorio=relatorio,
                tecnico=tecnico,
            )


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────


class DashboardView(TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        total_relatorios = RelatorioTecnico.objects.count()
        total_base = total_relatorios or 1

        total_pendentes = RelatorioTecnico.objects.filter(
            status=StatusRelatorio.PENDENTE
        ).count()

        total_adiantamentos = Adiantamento.objects.aggregate(total=Sum("valor"))[
            "total"
        ] or Decimal("0.00")

        total_itens = RelatorioTecnico.objects.aggregate(total=Sum("despesas__valor"))[
            "total"
        ] or Decimal("0.00")

        total_km_valor = RelatorioTecnico.objects.aggregate(
            total=Sum("trechos__valor_calculado")
        )["total"] or Decimal("0.00")

        total_despesas_valor = total_itens + total_km_valor
        total_tecnicos = Tecnico.objects.filter(ativo=True).count()
        total_clientes = Cliente.objects.filter(ativo=True).count()

        model_fields = {field.name for field in RelatorioTecnico._meta.get_fields()}
        select_related_fields = ["cliente"]
        if "tecnico" in model_fields:
            select_related_fields.append("tecnico")
        elif "tecnico_responsavel" in model_fields:
            select_related_fields.append("tecnico_responsavel")

        relatorios_recentes = RelatorioTecnico.objects.select_related(
            *select_related_fields
        ).order_by("-criado_em")[:8]

        status_keys = ["rascunho", "pendente", "fechado", "aprovado", "faturado"]
        status_choices = dict(getattr(StatusRelatorio, "choices", []))
        percentuais = {
            status: (
                round(
                    RelatorioTecnico.objects.filter(status=status).count()
                    / total_base
                    * 100
                )
                if status in status_choices
                else 0
            )
            for status in status_keys
        }

        def moeda(valor):
            return (
                f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            )

        ctx.update(
            {
                "titulo_pagina": "Dashboard",
                "total_relatorios": total_relatorios,
                "total_pendentes": total_pendentes,
                "total_adiantamentos_valor": total_adiantamentos,
                "total_despesas_valor": total_despesas_valor,
                "total_tecnicos": total_tecnicos,
                "total_clientes": total_clientes,
                "total_adiantamentos": moeda(total_adiantamentos),
                "total_despesas": moeda(total_despesas_valor),
                "cards": [
                    {
                        "titulo": "Total de Relatórios",
                        "valor": total_relatorios,
                        "icone": "bi-file-earmark-text",
                        "cor": "primary",
                        "rodape": f"{total_relatorios} relatórios cadastrados",
                    },
                    {
                        "titulo": "Adiantamentos",
                        "valor": moeda(total_adiantamentos),
                        "icone": "bi-cash-coin",
                        "cor": "success",
                        "rodape": "total lançado no sistema",
                    },
                    {
                        "titulo": "Pendentes",
                        "valor": total_pendentes,
                        "icone": "bi-hourglass-split",
                        "cor": "warning",
                        "rodape": "aguardando fechamento",
                    },
                    {
                        "titulo": "Total de Despesas",
                        "valor": moeda(total_despesas_valor),
                        "icone": "bi-graph-up-arrow",
                        "cor": "danger",
                        "rodape": "soma de itens e deslocamento",
                    },
                ],
                "relatorios_recentes": relatorios_recentes,
                "pct_rascunho": percentuais.get("rascunho", 0),
                "pct_pendente": percentuais.get("pendente", 0),
                "pct_fechado": percentuais.get("fechado", 0),
                "pct_aprovado": percentuais.get("aprovado", 0),
                "pct_faturado": percentuais.get("faturado", 0),
                "percentuais_status": percentuais,
            }
        )

        return ctx


# ─────────────────────────────────────────────
# LISTAGEM
# ─────────────────────────────────────────────


class RelatorioListView(ListView):
    model = RelatorioTecnico
    template_name = "relatorios/relatorio_list.html"
    context_object_name = "relatorios"
    paginate_by = 15

    def get_queryset(self):
        qs = RelatorioTecnico.objects.select_related("cliente", "tecnico_responsavel")
        form = RelatorioFiltroForm(self.request.GET)
        if form.is_valid():
            cd = form.cleaned_data
            if cd.get("tecnico"):
                qs = qs.filter(tecnico_responsavel=cd["tecnico"])
            if cd.get("cliente"):
                qs = qs.filter(cliente=cd["cliente"])
            if cd.get("status"):
                qs = qs.filter(status=cd["status"])
            if cd.get("data_inicio"):
                qs = qs.filter(data_inicio__gte=cd["data_inicio"])
            if cd.get("data_fim"):
                qs = qs.filter(data_fim__lte=cd["data_fim"])
            if cd.get("busca"):
                q = cd["busca"]
                qs = qs.filter(
                    Q(numero__icontains=q)
                    | Q(cliente__nome__icontains=q)
                    | Q(tecnico_responsavel__nome__icontains=q)
                    | Q(cidade_atendimento__icontains=q)
                )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_filtro"] = RelatorioFiltroForm(self.request.GET)
        ctx["titulo_pagina"] = "Relatórios Técnicos"
        ctx["total"] = self.get_queryset().count()
        return ctx


# ─────────────────────────────────────────────
# CRIAR / EDITAR
# ─────────────────────────────────────────────


def relatorio_form_view(request, pk=None):
    """
    View única para criar e editar relatórios.

    Regra de nomenclatura:
    - `valor_km`        → campo real no model Cliente (banco de dados)
    - `valor_km_padrao` → variável auxiliar local, repassada ao TrechoKmForm
                          via kwargs, e ao template para exibição/JS.
                          NUNCA é gravada no banco diretamente.
    """
    instance = get_object_or_404(RelatorioTecnico, pk=pk) if pk else None
    resumo_erros = []

    # ── Determinar valor_km_padrao (variável auxiliar) ────────────────────────
    # No POST: lê o cliente enviado no form para recalcular o padrão correto.
    # No GET:  lê o cliente já associado ao relatório (edição) ou 0 (criação).
    # Isso garante que linhas de KM novas já recebam o valor inicial correto.
    if request.method == "POST":
        cliente_id = request.POST.get("cliente")
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view POST: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )
    else:
        cliente_id = getattr(instance, "cliente_id", None) if instance else None
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view GET: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )

    # ── POST ──────────────────────────────────────────────────────────────────
    if request.method == "POST":
        form = RelatorioTecnicoForm(request.POST, request.FILES, instance=instance)

        fs_desp = ItemDespesaFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="despesas",
        )

        fs_km = TrechoKmFormSet(
            request.POST,
            instance=instance,
            prefix="trechos",
            form_kwargs={"valor_km_padrao": valor_km_padrao},
        )

        form_ok = form.is_valid()
        desp_ok = fs_desp.is_valid()
        km_ok = fs_km.is_valid()

        logger.debug(
            "Validação: form=%s | fs_desp=%s | fs_km=%s",
            form_ok,
            desp_ok,
            km_ok,
        )
        if not form_ok:
            logger.debug("Erros form principal: %s", form.errors)
        if not desp_ok:
            logger.debug("Erros fs_desp: %s", fs_desp.errors)
        if not km_ok:
            logger.debug("Erros fs_km: %s", fs_km.errors)

        if form_ok and desp_ok and km_ok:
            # ── Determinar ação ───────────────────────────────────────────────
            # "acao" vem do name/value do botão clicado:
            #   "rascunho" → botão Salvar rascunho
            #   "enviar"   → botão Salvar relatório (ou confirmação do modal)
            acao = request.POST.get("acao", "rascunho")
            relatorio = form.save(commit=False)

            if acao == "rascunho":
                relatorio.status = StatusRelatorio.RASCUNHO
            else:
                # "enviar" ou qualquer valor desconhecido → pendente
                relatorio.status = StatusRelatorio.PENDENTE

            tem_despesa = any(_form_has_content(f) for f in fs_desp.forms)

            # ── Validações extras (apenas para status que exigem) ─────────────
            # Usamos uma flag local em vez de re-checar .errors dos formsets,
            # porque fs_desp.errors retorna [{}, {}, ...] para forms vazios
            # (dicts vazios são falsy individualmente, mas a lista é truthy),
            # o que tornava o teste `not any(fs_desp.errors)` não confiável.
            erros_extras = False

            if (
                relatorio.status in {StatusRelatorio.FECHADO, StatusRelatorio.FATURADO}
                and not tem_despesa
            ):
                form.add_error(
                    None,
                    "É necessário informar ao menos um item de despesa para fechar o relatório.",
                )
                erros_extras = True

            elif relatorio.status == StatusRelatorio.FATURADO:
                # Comprovante obrigatório só para FATURADO (não para PENDENTE).
                # Para PENDENTE, o aviso é feito via modal no frontend — o
                # usuário pode confirmar e enviar sem comprovante.
                for f in fs_desp.forms:
                    if not _form_has_content(f):
                        continue
                    comprovante = f.cleaned_data.get("comprovante") or getattr(
                        f.instance, "comprovante", None
                    )
                    if not comprovante:
                        f.add_error(
                            "comprovante",
                            "Comprovante obrigatório para status Faturado.",
                        )
                        erros_extras = True
                        break

            # ── Salvar ────────────────────────────────────────────────────────
            # Condição limpa: só usa a flag local + form.errors do principal.
            # NÃO re-checa fs_desp.errors nem fs_km.errors aqui — eles já
            # foram validados pelo is_valid() acima e qualquer erro extra
            # foi capturado pela flag `erros_extras`.
            if not erros_extras and not form.errors:
                try:
                    with transaction.atomic():
                        relatorio.save()
                        form.save_m2m()

                        tecnicos_apoio = form.cleaned_data.get("tecnicos_equipe", [])
                        _sync_equipe(relatorio, tecnicos_apoio)

                        fs_desp.instance = relatorio
                        itens_salvos = fs_desp.save(commit=False)
                        for item in itens_salvos:
                            item.relatorio = relatorio
                            item.save()
                        for obj in fs_desp.deleted_objects:
                            obj.delete()

                        fs_km.instance = relatorio
                        trechos_salvos = fs_km.save(commit=False)
                        for trecho in trechos_salvos:
                            trecho.relatorio = relatorio
                            trecho.save()
                        for obj in fs_km.deleted_objects:
                            obj.delete()

                        logger.info(
                            "Relatório %s salvo (pk=%s, status=%s, acao=%s).",
                            relatorio.numero,
                            relatorio.pk,
                            relatorio.status,
                            acao,
                        )

                    messages.success(
                        request,
                        f"Relatório {relatorio.numero} salvo com sucesso.",
                    )
                    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

                except Exception as exc:
                    logger.exception("Erro ao salvar relatório: %s", exc)
                    messages.error(request, "Erro interno ao salvar. Tente novamente.")
                    # Não adiciona ao resumo_erros — é erro de infra, não de validação
                    return render(
                        request,
                        "relatorios/relatorio_form.html",
                        {
                            "form": form,
                            "fs_desp": fs_desp,
                            "fs_km": fs_km,
                            "instance": instance,
                            "titulo_pagina": (
                                f"Editar Relatório {instance.numero}"
                                if instance
                                else "Novo Relatório"
                            ),
                            "salvar_rascunho": "Salvar rascunho",
                            "enviar": (
                                "Salvar alterações" if instance else "Criar Relatório"
                            ),
                            "valor_km_padrao": str(valor_km_padrao),
                            "resumo_erros": [],
                        },
                    )

        # ── Chegou aqui = alguma validação falhou ─────────────────────────────
        messages.error(request, "Corrija os erros indicados antes de salvar.")

        if not form_ok:
            resumo_erros.append(
                "Dados Gerais possui campos inválidos ou obrigatórios não preenchidos."
            )
        if not desp_ok:
            qtd = sum(1 for e in fs_desp.errors if e)
            resumo_erros.append(f"Despesas possui {qtd} inconsistência(s).")
        if not km_ok:
            qtd = sum(1 for e in fs_km.errors if e)
            resumo_erros.append(f"Trechos/KM possui {qtd} inconsistência(s).")

    # ── GET ───────────────────────────────────────────────────────────────────
    else:
        form = RelatorioTecnicoForm(instance=instance)

        fs_desp = ItemDespesaFormSet(
            instance=instance,
            prefix="despesas",
        )

        fs_km = TrechoKmFormSet(
            instance=instance,
            prefix="trechos",
            form_kwargs={"valor_km_padrao": valor_km_padrao},
        )

    # ── Renderização (GET e POST com erro chegam aqui) ─────────────────────────
    return render(
        request,
        "relatorios/relatorio_form.html",
        {
            "form": form,
            "fs_desp": fs_desp,
            "fs_km": fs_km,
            "instance": instance,
            "titulo_pagina": (
                f"Editar Relatório {instance.numero}" if instance else "Novo Relatório"
            ),
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar alterações" if instance else "Criar Relatório",
            # valor_km_padrao aqui é APENAS para uso no template (JS, exibição).
            # Sempre string para evitar erros de template com None.
            "valor_km_padrao": str(valor_km_padrao),
            "resumo_erros": resumo_erros,
        },
    )


# Atalhos de URL mantidos por compatibilidade de roteamento
def relatorio_create(request):
    return relatorio_form_view(request)


def relatorio_update(request, pk):
    return relatorio_form_view(request, pk=pk)


# ─────────────────────────────────────────────
# LINHA PARCIAL — DESPESA (fetch via JS)
# ─────────────────────────────────────────────


def nova_linha_despesa(request):
    """
    Retorna o HTML de uma nova linha de despesa vazia.
    Chamada via fetch do JavaScript ao clicar em "Adicionar Despesa".
    """
    idx = request.GET.get("idx", 0)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0

    form = ItemDespesaForm(prefix=f"despesas-{idx}")

    return render(
        request,
        "partials/_linha_despesa.html",
        {
            "form": form,
            "idx": idx,
        },
    )


# ─────────────────────────────────────────────
# LINHA PARCIAL — TRECHO KM (fetch via JS)
# ─────────────────────────────────────────────


def nova_linha_km(request):
    """
    Retorna o HTML de uma nova linha de trecho KM.
    Chamada via fetch do JavaScript ao clicar em "Adicionar trecho".

    Recebe via GET:
    - idx             : índice da linha (int)
    - valor_km_padrao : valor R$/km do cliente atual (float como string)
                        É uma variável auxiliar — não é campo do model.
                        Usada apenas para pré-preencher o campo valor_km
                        na linha nova (initial do form).
    """
    # Índice da linha
    try:
        idx = int(request.GET.get("idx", 0))
    except (TypeError, ValueError):
        idx = 0

    # valor_km_padrao: vem como string do JS, pode ser None, "" ou "0"
    # Converte para float com fallback seguro para 0.0
    raw = request.GET.get("valor_km_padrao", "")
    try:
        valor_km_padrao = float(raw) if raw not in (None, "", "None") else 0.0
    except (TypeError, ValueError):
        logger.warning("nova_linha_km: valor_km_padrao inválido recebido: %r", raw)
        valor_km_padrao = 0.0

    logger.debug("nova_linha_km: idx=%s, valor_km_padrao=%s", idx, valor_km_padrao)

    form = TrechoKmForm(
        prefix=f"trechos-{idx}",
        valor_km_padrao=valor_km_padrao,  # repassado ao __init__ via kwargs.pop()
    )

    return render(
        request,
        "partials/_linha_trecho.html",
        {
            "form": form,
            "idx": idx,
        },
    )


# ─────────────────────────────────────────────
# DETALHE
# ─────────────────────────────────────────────


def relatorio_detail_view(request, pk):
    relatorio = get_object_or_404(
        RelatorioTecnico.objects.select_related(
            "cliente", "tecnico_responsavel"
        ).prefetch_related("despesas", "trechos", "equipe__tecnico"),
        pk=pk,
    )
    return render(
        request,
        "relatorios/relatorio_detail.html",
        {
            "relatorio": relatorio,
            "titulo_pagina": f"Relatório {relatorio.numero}",
        },
    )


# ─────────────────────────────────────────────
# EXCLUIR
# ─────────────────────────────────────────────


def relatorio_delete_view(request, pk):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    if request.method == "POST":
        numero = relatorio.numero
        relatorio.delete()
        messages.success(request, f"Relatório {numero} excluído.")
        return redirect("relatorios:relatorio_list")
    return render(
        request,
        "relatorios/relatorio_confirm_delete.html",
        {
            "object": relatorio,
            "titulo_pagina": "Excluir Relatório",
        },
    )


# ─────────────────────────────────────────────
# MUDAR STATUS
# ─────────────────────────────────────────────


@require_POST
def relatorio_status_view(request, pk, status):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    status_validos = [s[0] for s in StatusRelatorio.choices]

    if status not in status_validos:
        messages.error(request, "Status inválido.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    if status == StatusRelatorio.PENDENTE:
        erros = relatorio.pode_enviar()
        if erros:
            for e in erros:
                messages.error(request, e)
            return redirect("relatorios:relatorio_detail", pk=pk)

    relatorio.status = status
    relatorio.save(update_fields=["status", "atualizado_em"])
    messages.success(
        request,
        f'Status alterado para "{relatorio.get_status_display()}".',
    )
    return redirect("relatorios:relatorio_detail", pk=pk)


# ─────────────────────────────────────────────
# TÉCNICOS
# ─────────────────────────────────────────────


class TecnicoListView(ListView):
    model = Tecnico
    template_name = "tecnicos/tecnico_list.html"
    context_object_name = "tecnicos"
    paginate_by = 20

    def get_queryset(self):
        qs = Tecnico.objects.all()
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            qs = qs.filter(Q(nome__icontains=busca) | Q(email__icontains=busca))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Técnicos"
        ctx["busca"] = self.request.GET.get("busca", "")
        return ctx


def tecnico_form_view(request, pk=None):
    instance = get_object_or_404(Tecnico, pk=pk) if pk else None
    form = TecnicoForm(request.POST or None, instance=instance)
    if form.is_valid():
        t = form.save()
        messages.success(request, f"Técnico {t.nome} salvo!")
        return redirect("relatorios:tecnico_list")
    return render(
        request,
        "tecnicos/tecnico_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Técnico" if instance else "Novo Técnico",
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar alterações" if instance else "Cadastrar",
        },
    )


def tecnico_delete_view(request, pk):
    tecnico = get_object_or_404(Tecnico, pk=pk)
    if request.method == "POST":
        tecnico.delete()
        messages.success(request, "Técnico removido.")
        return redirect("relatorios:tecnico_list")
    return render(
        request,
        "tecnicos/tecnico_confirm_delete.html",
        {
            "object": tecnico,
            "titulo_pagina": "Excluir Técnico",
        },
    )


# ─────────────────────────────────────────────
# CLIENTES
# ─────────────────────────────────────────────


class ClienteListView(ListView):
    model = Cliente
    template_name = "clientes/cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 20

    def get_queryset(self):
        qs = Cliente.objects.all()
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(cnpj_cpf__icontains=busca)
                | Q(cidade__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Clientes"
        ctx["busca"] = self.request.GET.get("busca", "")
        return ctx


def cliente_form_view(request, pk=None):
    instance = get_object_or_404(Cliente, pk=pk) if pk else None
    form = ClienteForm(request.POST or None, instance=instance)
    if form.is_valid():
        c = form.save()
        messages.success(request, f"Cliente {c.nome} salvo!")
        return redirect("relatorios:cliente_list")
    return render(
        request,
        "clientes/cliente_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Cliente" if instance else "Novo Cliente",
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar" if instance else "Cadastrar",
        },
    )


def cliente_delete_view(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Cliente removido.")
        return redirect("relatorios:cliente_list")
    return render(
        request,
        "clientes/cliente_confirm_delete.html",
        {
            "object": cliente,
            "titulo_pagina": "Excluir Cliente",
        },
    )


# ─────────────────────────────────────────────
# ADIANTAMENTOS
# ─────────────────────────────────────────────


class AdiantamentoListView(ListView):
    model = Adiantamento
    template_name = "adiantamentos/adiantamento_list.html"
    context_object_name = "adiantamentos"
    paginate_by = 20

    def get_queryset(self):
        qs = Adiantamento.objects.select_related("tecnico", "relatorio")
        tecnico = self.request.GET.get("tecnico")
        if tecnico:
            qs = qs.filter(tecnico_id=tecnico)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Adiantamentos"
        ctx["tecnicos"] = Tecnico.objects.filter(ativo=True)
        ctx["tecnico_sel"] = self.request.GET.get("tecnico", "")
        ctx["total_geral"] = self.get_queryset().aggregate(t=Sum("valor"))[
            "t"
        ] or Decimal("0.00")
        return ctx


def adiantamento_form_view(request, pk=None):
    instance = get_object_or_404(Adiantamento, pk=pk) if pk else None
    form = AdiantamentoForm(request.POST or None, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, "Adiantamento salvo!")
        return redirect("relatorios:adiantamento_list")
    return render(
        request,
        "adiantamentos/adiantamento_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Adiantamento" if instance else "Novo Adiantamento",
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar" if instance else "Registrar",
        },
    )


def adiantamento_delete_view(request, pk):
    obj = get_object_or_404(Adiantamento, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Adiantamento removido.")
        return redirect("relatorios:adiantamento_list")
    return render(
        request,
        "adiantamentos/adiantamento_confirm_delete.html",
        {
            "object": obj,
            "titulo_pagina": "Excluir Adiantamento",
        },
    )
