import logging
import json
from decimal import Decimal

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView, TemplateView

from .models import (
    Adiantamento,
    Cliente,
    ItemDespesa,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusRelatorio,
    Tecnico,
    TipoEventoHistorico,
    TrechoKm,
)
from .services.historico_service import registrar_evento
from .services.clientes_relatorio_service import (
    normalizar_ids_clientes,
    obter_clientes_relatorio,
    sync_clientes_despesa,
    sync_clientes_relatorio,
    sync_clientes_trecho,
)
from .services.autorizacao_service import (
    exigir_acesso_erp,
    exigir_administrativo,
    exigir_financeiro,
    queryset_relatorios_visiveis,
    usuario_eh_administrativo,
    usuario_pode_acessar_erp,
    usuario_pode_atuar_como_financeiro,
    usuario_pode_editar_relatorio,
    usuario_pode_enviar_relatorio,
    usuario_pode_excluir_relatorio,
    usuario_pode_visualizar_relatorio,
    usuario_eh_superadmin,
)
from .services.workflow_service import (
    WorkflowError,
    aprovar_relatorio,
    enviar_para_conferencia,
    preparar_rascunho_para_salvar,
    rejeitar_relatorio,
    relatorio_bloqueado as workflow_relatorio_bloqueado,
    solicitar_ajuste,
)
from .services.rateio_service import (
    RateioError,
    garantir_rateio_despesa,
    garantir_rateio_trecho,
    garantir_rateios_relatorio,
    salvar_rateio_despesa,
    salvar_rateio_trecho,
    serializar_rateio,
)
from .services.resumo_cliente_service import resumo_financeiro_por_cliente
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

BLOQUEIO_POS_APROVACAO = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


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
        if k not in {"DELETE", "id", "relatorio", "ordem", "quem_pagou"}
    ]
    return any(v not in (None, "", [], ()) for v in values)


def _clientes_selecionados_do_request(request, instance=None):
    if request.method == "POST":
        ids = normalizar_ids_clientes(request.POST.get("clientes_relatorio"))
        nomes = list(Cliente.objects.filter(pk__in=ids).order_by("nome").values_list("nome", flat=True))
        return ids, nomes

    if instance:
        clientes = list(obter_clientes_relatorio(instance))
        return [cliente.pk for cliente in clientes], [cliente.nome for cliente in clientes]

    return [], []


def _tecnicos_selecionados_do_request(request, instance=None):
    if request.method == "POST":
        ids = []
        responsavel_id = request.POST.get("tecnico_responsavel")
        if responsavel_id:
            ids.append(responsavel_id)
        ids.extend(request.POST.getlist("tecnicos_equipe"))
        ids = normalizar_ids_clientes(ids)
        tecnicos = {
            tecnico.pk: tecnico.nome
            for tecnico in Tecnico.objects.filter(pk__in=ids)
        }
        nomes = [tecnicos[pk] for pk in ids if pk in tecnicos]
        return ids, nomes

    if instance:
        tecnicos = []
        if instance.tecnico_responsavel_id:
            tecnicos.append(instance.tecnico_responsavel)
        tecnicos.extend(instance.tecnicos_adicionais.order_by("nome"))
        return [tecnico.pk for tecnico in tecnicos], [tecnico.nome for tecnico in tecnicos]

    return [], []


def _clientes_item_post(request, prefix):
    return normalizar_ids_clientes(request.POST.get(f"{prefix}-clientes"))


def _validar_clientes_formsets(request, fs_desp, fs_km, cliente_ids_relatorio):
    erros = []
    clientes_relatorio = set(cliente_ids_relatorio)

    if not cliente_ids_relatorio:
        erros.append("Selecione ao menos um cliente para o relatório.")

    def linha_tem_conteudo(form):
        return _form_has_content(form)

    for form in fs_desp.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        if not linha_tem_conteudo(form):
            continue
        ids = _clientes_item_post(request, form.prefix)
        if not ids and len(clientes_relatorio) == 1:
            continue
        if not ids:
            erros.append("Toda despesa deve informar os clientes envolvidos.")
        elif set(ids) - clientes_relatorio:
            erros.append("Despesa referencia cliente fora do relatório.")

    for form in fs_km.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        if not linha_tem_conteudo(form):
            continue
        ids = _clientes_item_post(request, form.prefix)
        if not ids and len(clientes_relatorio) == 1:
            continue
        if not ids:
            erros.append("Todo trecho de KM deve informar os clientes envolvidos.")
        elif set(ids) - clientes_relatorio:
            erros.append("Trecho de KM referencia cliente fora do relatório.")

    return list(dict.fromkeys(erros))


def _diagnostico_exception_salvamento(exc):
    mensagem = str(exc)
    if "relatorios_despesarateio" in mensagem or "relatorios_trechorateiokm" in mensagem:
        return {
            "tipo": exc.__class__.__name__,
            "codigo": "RATEIO_MIGRATION_PENDENTE",
            "mensagem": (
                "As tabelas de rateio ainda não existem no banco. "
                "Execute python manage.py migrate no ambiente."
            ),
        }
    return {
        "tipo": exc.__class__.__name__,
        "codigo": "ERRO_INTERNO_SALVAMENTO",
        "mensagem": "Erro interno ao salvar relatório. Verifique o log do servidor.",
    }


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


def _duplicar_relatorio(original, usuario=None):
    novo = RelatorioTecnico(
        cliente=original.cliente,
        tecnico_responsavel=original.tecnico_responsavel,
        cidade_atendimento=original.cidade_atendimento,
        uf_atendimento=original.uf_atendimento,
        tipo_localidade=original.tipo_localidade,
        data_inicio=original.data_inicio,
        data_fim=original.data_fim,
        motivo=original.motivo,
        centro_custo=original.centro_custo,
        valor_adiantamento=Decimal("0.00"),
        observacoes=original.observacoes,
        status=StatusRelatorio.RASCUNHO,
        criado_por=usuario,
    )
    novo.save()
    registrar_evento(
        novo,
        usuario,
        TipoEventoHistorico.CRIADO,
        f"Rascunho criado a partir da duplicação do relatório {original.identificador}.",
        {"origem_relatorio_id": original.pk, "origem_numero": original.numero},
    )

    for apoio in original.equipe.select_related("tecnico").all():
        RelatorioTecnicoEquipe.objects.create(
            relatorio=novo,
            tecnico=apoio.tecnico,
            papel=apoio.papel,
        )

    despesas = [
        ItemDespesa(
            relatorio=novo,
            ordem=despesa.ordem,
            data=None,
            tipo=despesa.tipo,
            descricao=despesa.descricao,
            valor=despesa.valor,
            valor_aprovado=None,
            quem_pagou=despesa.quem_pagou,
            comprovante=None,
            observacoes=despesa.observacoes,
        )
        for despesa in original.despesas.all()
    ]
    if despesas:
        ItemDespesa.objects.bulk_create(despesas)

    trechos = [
        TrechoKm(
            relatorio=novo,
            ordem=trecho.ordem,
            data=None,
            origem=trecho.origem,
            destino=trecho.destino,
            km=trecho.km,
            valor_km=trecho.valor_km,
            valor_km_aprovado=None,
            observacao=trecho.observacao,
        )
        for trecho in original.trechos.all()
    ]
    for trecho in trechos:
        trecho.valor_calculado = (trecho.km * trecho.valor_km).quantize(
            Decimal("0.01")
        )
    if trechos:
        TrechoKm.objects.bulk_create(trechos)

    return novo


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────


def _formatar_data(data):
    return data.strftime("%d/%m/%Y") if data else "-"


def _formatar_moeda(valor):
    valor = valor or Decimal("0.00")
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _request_espera_json(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


def _lista_erros_operacionais(exc):
    erros = getattr(exc, "errors", None)
    if erros:
        return list(erros)
    return [str(exc)]


def _adicionar_erros_operacionais(request, erros):
    for erro in erros:
        messages.error(request, erro, extra_tags="operational-error")


def _itens_pdf_reembolso(relatorio):
    itens = []

    for despesa in relatorio.despesas.all():
        valor = despesa.valor_aprovado
        if valor is None:
            valor = despesa.valor
        valor = (valor or Decimal("0.00")).quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        itens.append(
            {
                "data": despesa.data,
                "documento": "Comprovante",
                "descricao": despesa.descricao,
                "valor": valor,
            }
        )

    for trecho in relatorio.trechos.all():
        valor_km = trecho.valor_km_aprovado
        if valor_km is None:
            valor = trecho.valor_calculado
        else:
            valor = (trecho.km * valor_km).quantize(Decimal("0.01"))
        valor = (valor or Decimal("0.00")).quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        itens.append(
            {
                "data": trecho.data,
                "documento": "Comprovante",
                "descricao": "Deslocamento",
                "valor": valor,
            }
        )

    itens.sort(key=lambda item: item["data"] or relatorio.data_inicio)
    return itens


def _usuario_pode_aprovar_financeiro(user):
    return usuario_pode_atuar_como_financeiro(user)


def _relatorio_bloqueado(relatorio):
    return workflow_relatorio_bloqueado(relatorio)


def _relatorio_editavel_por_usuario(relatorio, user):
    return usuario_pode_editar_relatorio(user, relatorio)


def _relatorios_visiveis(user, queryset=None):
    queryset = queryset if queryset is not None else RelatorioTecnico.objects.all()
    return queryset_relatorios_visiveis(user, queryset)


def _usuario_pode_ver_relatorio_ou_403(user, relatorio):
    if not usuario_pode_visualizar_relatorio(user, relatorio):
        raise RelatorioTecnico.DoesNotExist


def _relatorio_filtro_form(user, data=None):
    form = RelatorioFiltroForm(data)
    if not usuario_eh_administrativo(user):
        relatorios = _relatorios_visiveis(user, RelatorioTecnico.objects.all())
        form.fields["tecnico"].queryset = Tecnico.objects.filter(
            pk__in=relatorios.values("tecnico_responsavel_id")
        )
        form.fields["cliente"].queryset = Cliente.objects.filter(
            pk__in=relatorios.values("cliente_id")
        )
    return form

class AcessoErpMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):

        print("DEBUG USER:", request.user)
        print("DEBUG AUTH:", request.user.is_authenticated)

        # deixa o LoginRequiredMixin agir primeiro
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        print("DEBUG GROUPS:", list(request.user.groups.values_list("name", flat=True)))
        print("DEBUG SUPERUSER:", request.user.is_superuser)
        print("DEBUG ERP:", usuario_pode_acessar_erp(request.user))

        if not usuario_pode_acessar_erp(request.user):
            messages.error(request, "Seu usuário não possui perfil de acesso ao ERP.")
            raise PermissionDenied("Usuário sem grupo ERP.")

        return super().dispatch(request, *args, **kwargs)


class AdministrativoMixin(AcessoErpMixin):
    def dispatch(self, request, *args, **kwargs):
        if not usuario_eh_administrativo(request.user):
            messages.error(request, "VocÃª nÃ£o tem permissÃ£o para acessar esta Ã¡rea.")
            return redirect("relatorios:dashboard")
        return super().dispatch(request, *args, **kwargs)


def _avisos_financeiro(relatorio):
    avisos = []
    despesas = list(relatorio.despesas.all())
    trechos = list(relatorio.trechos.all())

    despesas_por_data = {}
    despesas_duplicadas = {}
    despesas_sem_comprovante = []
    despesas_altas_sem_observacao = []

    for idx, despesa in enumerate(despesas, start=1):
        linha_id = f"linha-despesa-{idx}"
        descricao = (despesa.descricao or "").strip()
        observacoes = (despesa.observacoes or "").strip()

        if descricao and len(descricao) < 4:
            avisos.append(
                {
                    "tipo": "descricao_curta",
                    "mensagem": f"Verificar item {idx}: descrição pode não ter sido preenchida corretamente.",
                    "linha_id": linha_id,
                    "linha_ids": [linha_id],
                    "icone": "bi-pencil-square",
                }
            )

        if despesa.tipo == "alimentacao" and despesa.data:
            despesas_por_data.setdefault(despesa.data, []).append((idx, despesa))

        if despesa.valor and despesa.valor > Decimal("300.00") and not observacoes:
            despesas_altas_sem_observacao.append((idx, despesa))

        if not despesa.comprovante:
            despesas_sem_comprovante.append((idx, despesa))

        chave_duplicada = (despesa.data, despesa.tipo, despesa.valor)
        despesas_duplicadas.setdefault(chave_duplicada, []).append((idx, despesa))

    if despesas_altas_sem_observacao:
        linha_ids = [
            f"linha-despesa-{idx}" for idx, _despesa in despesas_altas_sem_observacao
        ]
        for idx, _despesa in despesas_altas_sem_observacao:
            linha_id = f"linha-despesa-{idx}"
            avisos.append(
                {
                    "tipo": "despesa_alta_sem_observacao",
                    "mensagem": "Despesa alta sem detalhamento em observações.",
                    "linha_id": linha_id,
                    "linha_ids": linha_ids,
                    "icone": "bi-cash-coin",
                }
            )

    for data, itens in despesas_por_data.items():
        if len(itens) >= 4:
            ultimo_idx = itens[-1][0]
            linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in itens]
            avisos.append(
                {
                    "tipo": "muitas_refeicoes",
                    "mensagem": f"O usuário incluiu {len(itens)} refeições na data {_formatar_data(data)}.",
                    "linha_id": f"linha-despesa-{ultimo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-cup-hot",
                }
            )

    if despesas_sem_comprovante:
        primeiro_idx = despesas_sem_comprovante[0][0]
        linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in despesas_sem_comprovante]
        avisos.append(
            {
                "tipo": "falta_comprovante",
                "mensagem": f"{len(despesas_sem_comprovante)} despesas foram enviadas sem comprovante.",
                "linha_id": f"linha-despesa-{primeiro_idx}",
                "linha_ids": linha_ids,
                "icone": "bi-paperclip",
            }
        )

    for itens in despesas_duplicadas.values():
        if len(itens) >= 2:
            segundo_idx = itens[1][0]
            linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in itens]
            avisos.append(
                {
                    "tipo": "despesa_duplicada",
                    "mensagem": "Possível despesa duplicada encontrada.",
                    "linha_id": f"linha-despesa-{segundo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-files",
                }
            )

    trechos_por_data = {}
    trechos_valor_km_divergente = []
    valor_km_padrao = relatorio.cliente.valor_km if relatorio.cliente_id else None

    for idx, trecho in enumerate(trechos, start=1):
        linha_id = f"linha-trecho-{idx}"

        if trecho.data:
            trechos_por_data.setdefault(trecho.data, []).append((idx, trecho))

        if valor_km_padrao and trecho.valor_km != valor_km_padrao:
            trechos_valor_km_divergente.append((idx, trecho))

    if trechos_valor_km_divergente:
        linha_ids = [
            f"linha-trecho-{idx}" for idx, _trecho in trechos_valor_km_divergente
        ]
        for idx, _trecho in trechos_valor_km_divergente:
            linha_id = f"linha-trecho-{idx}"
            avisos.append(
                {
                    "tipo": "valor_km_divergente",
                    "mensagem": f"Verificar trecho {idx}: valor KM diferente do padrão do cliente.",
                    "linha_id": linha_id,
                    "linha_ids": linha_ids,
                    "icone": "bi-speedometer2",
                }
            )

    for data, itens in trechos_por_data.items():
        if len(itens) >= 5:
            ultimo_idx = itens[-1][0]
            linha_ids = [f"linha-trecho-{idx}" for idx, _trecho in itens]
            avisos.append(
                {
                    "tipo": "muitos_deslocamentos",
                    "mensagem": f"O usuário incluiu muitos deslocamentos na data {_formatar_data(data)}.",
                    "linha_id": f"linha-trecho-{ultimo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-geo-alt",
                }
            )

    return avisos


class DashboardView(AcessoErpMixin, TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        administrativo = usuario_eh_administrativo(self.request.user)
        qs_relatorios = _relatorios_visiveis(self.request.user, RelatorioTecnico.objects.all())
        total_relatorios = qs_relatorios.count()
        total_base = total_relatorios or 1

        total_conferencia = qs_relatorios.filter(
            status=StatusRelatorio.CONFERENCIA
        ).count()

        total_adiantamentos = (
            Adiantamento.objects.aggregate(total=Sum("valor"))["total"]
            if administrativo
            else Decimal("0.00")
        ) or Decimal("0.00")

        total_itens = qs_relatorios.aggregate(total=Sum("despesas__valor"))[
            "total"
        ] or Decimal("0.00")

        total_km_valor = qs_relatorios.aggregate(
            total=Sum("trechos__valor_calculado")
        )["total"] or Decimal("0.00")

        total_despesas_valor = total_itens + total_km_valor
        total_tecnicos = Tecnico.objects.filter(ativo=True).count() if administrativo else 0
        total_clientes = Cliente.objects.filter(ativo=True).count() if administrativo else 0

        model_fields = {field.name for field in RelatorioTecnico._meta.get_fields()}
        select_related_fields = ["cliente"]
        if "tecnico" in model_fields:
            select_related_fields.append("tecnico")
        elif "tecnico_responsavel" in model_fields:
            select_related_fields.append("tecnico_responsavel")

        relatorios_recentes = qs_relatorios.select_related(
            *select_related_fields
        ).order_by("-criado_em")[:8]

        status_keys = [
            StatusRelatorio.RASCUNHO,
            StatusRelatorio.CONFERENCIA,
            StatusRelatorio.AJUSTE,
            StatusRelatorio.APROVADO,
            StatusRelatorio.REJEITADO,
        ]
        status_choices = dict(getattr(StatusRelatorio, "choices", []))
        percentuais = {
            status: (
                round(
                    qs_relatorios.filter(status=status).count()
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
                "dashboard_global": administrativo,
                "dashboard_individual": not administrativo,
                "total_relatorios": total_relatorios,
                "total_pendentes": total_conferencia,
                "total_conferencia": total_conferencia,
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
                        "titulo": "Conferência",
                        "valor": total_conferencia,
                        "icone": "bi-hourglass-split",
                        "cor": "warning",
                        "rodape": "aguardando conferÃªncia",
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
                "pct_pendente": percentuais.get(StatusRelatorio.CONFERENCIA, 0),
                "pct_conferencia": percentuais.get(StatusRelatorio.CONFERENCIA, 0),
                "pct_ajuste": percentuais.get(StatusRelatorio.AJUSTE, 0),
                "pct_aprovado": percentuais.get("aprovado", 0),
                "percentuais_status": percentuais,
            }
        )

        return ctx


# ─────────────────────────────────────────────
# LISTAGEM
# ─────────────────────────────────────────────


class RelatorioListView(AcessoErpMixin, ListView):
    model = RelatorioTecnico
    template_name = "relatorios/relatorio_list.html"
    context_object_name = "relatorios"
    paginate_by = 15

    def get_queryset(self):
        qs = _relatorios_visiveis(
            self.request.user,
            RelatorioTecnico.objects.select_related(
                "cliente", "tecnico_responsavel"
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "equipe__tecnico",
            ),
        )
        form = _relatorio_filtro_form(self.request.user, self.request.GET)
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
        ctx["form_filtro"] = _relatorio_filtro_form(self.request.user, self.request.GET)
        ctx["titulo_pagina"] = "Relatórios Técnicos"
        ctx["total"] = self.get_queryset().count()
        return ctx


# ─────────────────────────────────────────────
# CRIAR / EDITAR
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def relatorio_form_view(request, pk=None):
    """
    View única para criar e editar relatórios.

    Regra de nomenclatura:
    - `valor_km`        → campo real no model Cliente (banco de dados)
    - `valor_km_padrao` → variável auxiliar local, repassada ao TrechoKmForm
                          via kwargs, e ao template para exibição/JS.
                          NUNCA é gravada no banco diretamente.
    """
    instance = (
        get_object_or_404(
            _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
            pk=pk,
        )
        if pk
        else None
    )
    if instance and not _relatorio_editavel_por_usuario(instance, request.user):
        if instance.status == StatusRelatorio.AJUSTE:
            messages.error(request, "Relatório em ajuste deve ser editado pelo técnico.")
        else:
            messages.error(request, "Relatório aprovado ou rejeitado não pode ser editado.")
        return redirect("relatorios:relatorio_detail", pk=instance.pk)

    resumo_erros = []

    # ── Determinar valor_km_padrao (variável auxiliar) ────────────────────────
    # No POST: lê o cliente enviado no form para recalcular o padrão correto.
    # No GET:  lê o cliente já associado ao relatório (edição) ou 0 (criação).
    # Isso garante que linhas de KM novas já recebam o valor inicial correto.
    if request.method == "POST":
        clientes_post_ids, clientes_post_nomes = _clientes_selecionados_do_request(
            request,
            instance,
        )
        tecnicos_post_ids, tecnicos_post_nomes = _tecnicos_selecionados_do_request(
            request,
            instance,
        )
        cliente_id = clientes_post_ids[0] if clientes_post_ids else request.POST.get("cliente")
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view POST: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )
    else:
        clientes_post_ids, clientes_post_nomes = _clientes_selecionados_do_request(
            request,
            instance,
        )
        tecnicos_post_ids, tecnicos_post_nomes = _tecnicos_selecionados_do_request(
            request,
            instance,
        )
        cliente_id = getattr(instance, "cliente_id", None) if instance else None
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view GET: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )

    # ── POST ──────────────────────────────────────────────────────────────────
    if request.method == "POST":
        form = RelatorioTecnicoForm(
            request.POST,
            request.FILES,
            instance=instance,
        )

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
            cliente_ids_relatorio = normalizar_ids_clientes(
                request.POST.get("clientes_relatorio")
            )
            erros_clientes = _validar_clientes_formsets(
                request,
                fs_desp,
                fs_km,
                cliente_ids_relatorio,
            )
            if erros_clientes:
                resumo_erros.extend(erros_clientes)
                form_ok = False

        if form_ok and desp_ok and km_ok:
            # ── Determinar ação ───────────────────────────────────────────────
            # "acao" vem do name/value do botão clicado:
            #   "rascunho" → botão Salvar rascunho
            #   "enviar"   → botão Salvar relatório (ou confirmação do modal)
            acao = request.POST.get("acao", "rascunho")
            relatorio = form.save(commit=False)

            relatorio = preparar_rascunho_para_salvar(relatorio, instance)

            erros_extras = False

            # ── Salvar ────────────────────────────────────────────────────────
            # Condição limpa: só usa a flag local + form.errors do principal.
            # NÃO re-checa fs_desp.errors nem fs_km.errors aqui — eles já
            # foram validados pelo is_valid() acima e qualquer erro extra
            # foi capturado pela flag `erros_extras`.
            if not erros_extras and not form.errors:
                try:
                    with transaction.atomic():
                        relatorio_novo = instance is None
                        if relatorio_novo:
                            relatorio.criado_por = request.user
                        relatorio.cliente_id = cliente_ids_relatorio[0]
                        relatorio.save()
                        form.save_m2m()
                        sync_clientes_relatorio(relatorio, cliente_ids_relatorio)

                        tecnicos_apoio = form.cleaned_data.get("tecnicos_equipe", [])
                        _sync_equipe(relatorio, tecnicos_apoio)

                        fs_desp.instance = relatorio
                        for f in fs_desp.forms:
                            if not _form_has_content(f):
                                continue
                            item = f.save(commit=False)
                            item.relatorio = relatorio
                            item.save()
                            erros_item = sync_clientes_despesa(
                                item,
                                _clientes_item_post(request, f.prefix),
                            )
                            if erros_item:
                                raise WorkflowError(erros_item)
                        for f in fs_desp.deleted_forms:
                            if f.instance.pk:
                                f.instance.delete()

                        fs_km.instance = relatorio
                        for f in fs_km.forms:
                            if not _form_has_content(f):
                                continue
                            trecho = f.save(commit=False)
                            trecho.relatorio = relatorio
                            trecho.save()
                            erros_trecho = sync_clientes_trecho(
                                trecho,
                                _clientes_item_post(request, f.prefix),
                            )
                            if erros_trecho:
                                raise WorkflowError(erros_trecho)
                        for f in fs_km.deleted_forms:
                            if f.instance.pk:
                                f.instance.delete()

                        usuario_historico = (
                            request.user if request.user.is_authenticated else None
                        )
                        if relatorio_novo:
                            registrar_evento(
                                relatorio,
                                usuario_historico,
                                TipoEventoHistorico.CRIADO,
                                f"Rascunho {relatorio.identificador} criado.",
                            )
                        if acao != "rascunho":
                            relatorio = enviar_para_conferencia(
                                relatorio.pk,
                                usuario_historico,
                            )

                        logger.info(
                            "Relatório %s salvo (pk=%s, status=%s, acao=%s).",
                            relatorio.identificador,
                            relatorio.pk,
                            relatorio.status,
                            acao,
                        )

                    messages.success(
                        request,
                        f"Relatório {relatorio.identificador} salvo com sucesso.",
                    )
                    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

                except WorkflowError as exc:
                    erros = _lista_erros_operacionais(exc)
                    _adicionar_erros_operacionais(request, erros)
                    resumo_erros.extend(erros)
                    return render(
                        request,
                        "relatorios/relatorio_form.html",
                        {
                            "form": form,
                            "fs_desp": fs_desp,
                            "fs_km": fs_km,
                            "instance": instance,
                            "clientes_importacao": Cliente.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "tecnicos_importacao": Tecnico.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "titulo_pagina": (
                                f"Editar Relatório {instance.identificador}"
                                if instance
                                else "Novo Relatório"
                            ),
                            "valor_km_padrao": valor_km_padrao,
                            "resumo_erros": resumo_erros,
                            "clientes_selecionados_ids": clientes_post_ids,
                            "clientes_selecionados_nomes": clientes_post_nomes,
                            "tecnicos_selecionados_ids": tecnicos_post_ids,
                            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
                        },
                    )
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
                            "clientes_importacao": Cliente.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "tecnicos_importacao": Tecnico.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "titulo_pagina": (
                                f"Editar Relatório {instance.identificador}"
                                if instance
                                else "Novo Relatório"
                            ),
                            "salvar_rascunho": "Salvar rascunho",
                            "enviar": (
                                "Salvar alterações" if instance else "Criar Relatório"
                            ),
                            "valor_km_padrao": str(valor_km_padrao),
                            "resumo_erros": [diagnostico_backend["mensagem"]],
                            "diagnostico_backend": diagnostico_backend,
                            "clientes_selecionados_ids": clientes_post_ids,
                            "clientes_selecionados_nomes": clientes_post_nomes,
                            "tecnicos_selecionados_ids": tecnicos_post_ids,
                            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
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
            "clientes_importacao": Cliente.objects.filter(ativo=True).order_by("nome"),
            "tecnicos_importacao": Tecnico.objects.filter(ativo=True).order_by("nome"),
            "titulo_pagina": (
                f"Editar Relatório {instance.identificador}" if instance else "Novo Relatório"
            ),
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar alterações" if instance else "Criar Relatório",
            # valor_km_padrao aqui é APENAS para uso no template (JS, exibição).
            # Sempre string para evitar erros de template com None.
            "valor_km_padrao": str(valor_km_padrao),
            "resumo_erros": resumo_erros,
            "clientes_selecionados_ids": clientes_post_ids,
            "clientes_selecionados_nomes": clientes_post_nomes,
            "tecnicos_selecionados_ids": tecnicos_post_ids,
            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
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


@login_required
@exigir_acesso_erp
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


@login_required
@exigir_acesso_erp
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


@login_required
@exigir_acesso_erp
def relatorio_detail_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente", "tecnico_responsavel"
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "despesas__rateios__cliente",
                "trechos__clientes_vinculados__cliente",
                "trechos__rateios__cliente",
                "equipe__tecnico",
                "historicos__usuario",
            ),
        ),
        pk=pk,
    )
    inconsistencias_rateio = []
    try:
        garantir_rateios_relatorio(relatorio)
    except RateioError as exc:
        inconsistencias_rateio = [str(exc)]
    relatorio = (
        RelatorioTecnico.objects.select_related("cliente", "tecnico_responsavel")
        .prefetch_related(
            "clientes_vinculados__cliente",
            "despesas__clientes_vinculados__cliente",
            "despesas__rateios__cliente",
            "trechos__clientes_vinculados__cliente",
            "trechos__rateios__cliente",
            "equipe__tecnico",
            "historicos__usuario",
        )
        .get(pk=relatorio.pk)
    )
    distribuicao_clientes = resumo_financeiro_por_cliente(relatorio)

    return render(
        request,
        "relatorios/relatorio_detail.html",
        {
            "relatorio": relatorio,
            "avisos_financeiro": (
                _avisos_financeiro(relatorio)
                if usuario_pode_atuar_como_financeiro(request.user)
                else []
            ),
            "pode_editar_relatorio": _relatorio_editavel_por_usuario(
                relatorio, request.user
            ),
            "pode_atuar_financeiro": usuario_pode_atuar_como_financeiro(request.user),
            "pode_alterar_itens_financeiros": (
                usuario_eh_superadmin(request.user)
                or (
                    usuario_pode_atuar_como_financeiro(request.user)
                    and relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}
                )
            ),
            "superadmin_django": usuario_eh_superadmin(request.user),
            "pode_enviar_relatorio": usuario_pode_enviar_relatorio(request.user, relatorio),
            "pode_excluir_relatorio": usuario_pode_excluir_relatorio(request.user, relatorio),
            "inconsistencias_rateio": inconsistencias_rateio,
            "distribuicao_clientes": distribuicao_clientes,
            "titulo_pagina": f"Relatório {relatorio.identificador}",
        },
    )


# ─────────────────────────────────────────────
# EXCLUIR
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def relatorio_reembolso_pdf_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
            ).prefetch_related(
                "despesas",
                "trechos",
            ),
        ),
        pk=pk,
    )
    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF oficial só pode ser gerado após aprovação.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    itens = _itens_pdf_reembolso(relatorio)
    total = sum((item["valor"] for item in itens), Decimal("0.00"))
    emitido_em = timezone.localtime(timezone.now())

    html = render_to_string(
        "pdf/relatorio_reembolso.html",
        {
            "relatorio": relatorio,
            "itens": itens,
            "total": total,
            "emitido_em": emitido_em,
            "empresa": "CONTROL SUL GESTÃO EMPRESARIAL",
        },
        request=request,
    )

    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        logger.exception("WeasyPrint não está disponível: %s", exc)
        messages.error(
            request,
            "WeasyPrint não está disponível neste ambiente. Verifique a instalação das dependências nativas.",
        )
        return redirect("relatorios:relatorio_detail", pk=pk)

    css_path = settings.BASE_DIR / "templates" / "pdf" / "relatorio_reembolso.css"
    pdf = HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-reembolso-{relatorio.numero}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
@exigir_financeiro
def relatorio_pdf_interno_view(request, pk):
    relatorio = get_object_or_404(
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
            "aprovado_por",
        ).prefetch_related(
            "despesas",
            "trechos",
            "equipe__tecnico",
            "historicos__usuario",
        ),
        pk=pk,
    )
    emitido_em = timezone.localtime(timezone.now())
    usuario_gerador = request.user if request.user.is_authenticated else None
    historicos_resumidos = relatorio.historicos.all()[:10]
    anexos = [despesa for despesa in relatorio.despesas.all() if despesa.comprovante]

    html = render_to_string(
        "relatorios/pdf/interno.html",
        {
            "relatorio": relatorio,
            "empresa": "CONTROL SUL GESTÃO EMPRESARIAL",
            "emitido_em": emitido_em,
            "usuario_gerador": usuario_gerador,
            "avisos_financeiro": _avisos_financeiro(relatorio),
            "historicos_resumidos": historicos_resumidos,
            "anexos": anexos,
        },
        request=request,
    )

    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        logger.exception("WeasyPrint não está disponível: %s", exc)
        messages.error(
            request,
            "WeasyPrint não está disponível neste ambiente. Verifique a instalação das dependências nativas.",
        )
        return redirect("relatorios:relatorio_detail", pk=pk)

    css_path = settings.BASE_DIR / "static" / "css" / "pdf-relatorio.css"
    pdf = HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-interno-{relatorio.numero}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
@exigir_acesso_erp
def relatorio_delete_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
        pk=pk,
    )
    if usuario_pode_excluir_relatorio(request.user, relatorio):
        if request.method == "POST":
            numero = relatorio.identificador
            relatorio.delete()
            messages.success(request, f"RelatÃ³rio {numero} excluÃ­do.")
            return redirect("relatorios:relatorio_list")
        return render(
            request,
            "relatorios/relatorio_confirm_delete.html",
            {
                "object": relatorio,
                "titulo_pagina": "Excluir RelatÃ³rio",
            },
        )
    messages.error(request, "ExclusÃ£o de relatÃ³rios estÃ¡ bloqueada por polÃ­tica operacional.")
    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    if _relatorio_bloqueado(relatorio):
        messages.error(request, "Relatório aprovado ou rejeitado não pode ser excluído.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    if request.method == "POST":
        numero = relatorio.identificador
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


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_import_list_json(request):
    qs = _relatorios_visiveis(
        request.user,
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
        ),
    ).order_by("-data_inicio", "-criado_em")

    cliente_id = request.GET.get("cliente")
    tecnico_id = request.GET.get("tecnico")
    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")
    busca = (request.GET.get("busca") or "").strip()
    excluir = request.GET.get("excluir")

    if cliente_id:
        qs = qs.filter(cliente_id=cliente_id)
    if tecnico_id:
        qs = qs.filter(tecnico_responsavel_id=tecnico_id)
    if data_inicio:
        qs = qs.filter(data_inicio__gte=data_inicio)
    if data_fim:
        qs = qs.filter(data_fim__lte=data_fim)
    if busca:
        qs = qs.filter(
            Q(numero__icontains=busca)
            | Q(cliente__nome__icontains=busca)
            | Q(tecnico_responsavel__nome__icontains=busca)
            | Q(cidade_atendimento__icontains=busca)
            | Q(motivo__icontains=busca)
        )
    if excluir:
        qs = qs.exclude(pk=excluir)

    relatorios = [
        {
            "id": relatorio.pk,
            "numero": relatorio.identificador,
            "data": _formatar_data(relatorio.data_inicio),
            "cliente": relatorio.cliente.nome,
            "tecnico": relatorio.tecnico_responsavel.nome,
            "status": relatorio.get_status_display(),
            "total": _formatar_moeda(relatorio.total_despesas),
        }
        for relatorio in qs[:30]
    ]
    return JsonResponse({"relatorios": relatorios})


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_import_detail_json(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
            ).prefetch_related(
                "equipe__tecnico",
                "despesas",
                "trechos",
            ),
        ),
        pk=pk,
    )

    return JsonResponse(
        {
            "id": relatorio.pk,
            "numero": relatorio.identificador,
            "cliente_id": relatorio.cliente_id,
            "tecnico_id": relatorio.tecnico_responsavel_id,
            "apoio_ids": list(relatorio.equipe.values_list("tecnico_id", flat=True)),
            "despesas": [
                {
                    "tipo": despesa.tipo,
                    "descricao": despesa.descricao,
                    "valor": str(despesa.valor),
                    "observacoes": despesa.observacoes,
                }
                for despesa in relatorio.despesas.all()
            ],
            "trechos": [
                {
                    "origem": trecho.origem,
                    "destino": trecho.destino,
                    "km": str(trecho.km),
                    "valor_km": str(trecho.valor_km),
                }
                for trecho in relatorio.trechos.all()
            ],
        }
    )


@require_POST
@login_required
@exigir_acesso_erp
def relatorio_duplicate_view(request, pk):
    original = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
            ).prefetch_related(
                "equipe__tecnico",
                "despesas",
                "trechos",
            ),
        ),
        pk=pk,
    )

    try:
        with transaction.atomic():
            usuario_historico = request.user if request.user.is_authenticated else None
            novo = _duplicar_relatorio(original, usuario_historico)
    except Exception as exc:
        logger.exception("Erro ao duplicar relatÃ³rio %s: %s", pk, exc)
        messages.error(request, "Erro interno ao duplicar relatÃ³rio. Tente novamente.")
        return redirect("relatorios:relatorio_list")

    messages.success(
        request,
        f"Relatório {original.identificador} duplicado como {novo.identificador}.",
    )
    return redirect("relatorios:relatorio_update", pk=novo.pk)


@require_POST
@login_required
@exigir_financeiro
def relatorio_item_financeiro_view(request, pk, tipo, item_pk, acao):
    if tipo not in {"despesa", "trecho"} or acao not in {"rejeitar", "restaurar"}:
        messages.error(request, "Ação inválida para o item.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    try:
        with transaction.atomic():
            relatorio = get_object_or_404(
                RelatorioTecnico.objects.select_for_update(), pk=pk
            )
            if _relatorio_bloqueado(relatorio):
                messages.error(
                    request,
                    "Relatório aprovado ou rejeitado está bloqueado para alterações.",
                )
                return redirect("relatorios:relatorio_detail", pk=pk)

            modelo = ItemDespesa if tipo == "despesa" else TrechoKm
            item = get_object_or_404(
                modelo.objects.select_for_update(),
                pk=item_pk,
                relatorio=relatorio,
            )

            usuario_historico = (
                request.user if request.user.is_authenticated else None
            )

            if acao == "rejeitar":
                motivo = (
                    request.POST.get("motivo_rejeicao")
                    or request.POST.get("motivo_recusa")
                    or ""
                ).strip()
                if not motivo:
                    messages.error(request, "Informe a justificativa da rejeição do item.")
                    return redirect("relatorios:relatorio_detail", pk=pk)

                agora = timezone.now()
                item.rejeitado = True
                item.motivo_rejeicao = motivo
                item.rejeitado_por = usuario_historico
                item.rejeitado_em = agora
                item.status_financeiro = StatusFinanceiroItem.REJEITADO
                item.motivo_recusa = motivo
                item.save(
                    update_fields=[
                        "rejeitado",
                        "motivo_rejeicao",
                        "rejeitado_por",
                        "rejeitado_em",
                        "status_financeiro",
                        "motivo_recusa",
                    ]
                )
                if tipo == "despesa":
                    garantir_rateio_despesa(item)
                else:
                    garantir_rateio_trecho(item)

                if tipo == "despesa":
                    descricao = f"Despesa rejeitada pelo financeiro: {motivo}"
                else:
                    descricao = f"Trecho KM rejeitado pelo financeiro: {motivo}"

                registrar_evento(
                    relatorio,
                    usuario_historico,
                    TipoEventoHistorico.ITEM_REJEITADO,
                    descricao,
                    {
                        "tipo_item": tipo,
                        "item_id": item.pk,
                        "motivo": motivo,
                    },
                )
                messages.success(request, "Item removido do reembolso.")

            else:
                item.rejeitado = False
                item.motivo_rejeicao = ""
                item.rejeitado_por = None
                item.rejeitado_em = None
                item.status_financeiro = StatusFinanceiroItem.APROVADO
                item.motivo_recusa = ""
                item.save(
                    update_fields=[
                        "rejeitado",
                        "motivo_rejeicao",
                        "rejeitado_por",
                        "rejeitado_em",
                        "status_financeiro",
                        "motivo_recusa",
                    ]
                )
                if tipo == "despesa":
                    garantir_rateio_despesa(item)
                else:
                    garantir_rateio_trecho(item)
                registrar_evento(
                    relatorio,
                    usuario_historico,
                    TipoEventoHistorico.ITEM_REATIVADO,
                    "Item restaurado pelo financeiro.",
                    {
                        "tipo_item": tipo,
                        "item_id": item.pk,
                    },
                )
                messages.success(request, "Item restaurado para o reembolso.")

    except Exception as exc:
        logger.exception("Erro ao alterar item financeiro do relatório %s: %s", pk, exc)
        messages.error(request, "Erro interno ao alterar item. Tente novamente.")

    return redirect("relatorios:relatorio_detail", pk=pk)


@require_POST
@login_required
@exigir_financeiro
def relatorio_rateio_financeiro_json(request, pk, tipo, item_pk):
    if tipo not in {"despesa", "trecho"}:
        return JsonResponse({"success": False, "errors": ["Tipo de item inválido."]}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "errors": ["JSON inválido."]}, status=400)

    try:
        relatorio = get_object_or_404(
            _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
            pk=pk,
        )
        if _relatorio_bloqueado(relatorio):
            return JsonResponse(
                {"success": False, "errors": ["Relatório aprovado ou rejeitado está bloqueado."]},
                status=400,
            )

        modelo = ItemDespesa if tipo == "despesa" else TrechoKm
        item = get_object_or_404(modelo, pk=item_pk, relatorio=relatorio)
        acao = payload.get("acao") or "salvar"
        aprovar = acao == "aprovar"
        dados_rateio = payload.get("rateios") or []
        motivo = payload.get("motivo") or ""

        if tipo == "despesa":
            garantir_rateio_despesa(item)
            rateios = salvar_rateio_despesa(
                item,
                dados_rateio,
                request.user,
                motivo=motivo,
                aprovar=aprovar,
            )
        else:
            garantir_rateio_trecho(item)
            rateios = salvar_rateio_trecho(
                item,
                dados_rateio,
                request.user,
                motivo=motivo,
                aprovar=aprovar,
            )

        return JsonResponse(
            {
                "success": True,
                "rateios": [serializar_rateio(rateio) for rateio in rateios],
                "message": "Rateio salvo com sucesso.",
            }
        )
    except RateioError as exc:
        return JsonResponse({"success": False, "errors": [str(exc)]}, status=400)
    except Exception as exc:
        logger.exception("Erro ao salvar rateio do relatório %s: %s", pk, exc)
        return JsonResponse(
            {"success": False, "errors": ["Erro interno ao salvar rateio."]},
            status=500,
        )


@require_POST
@login_required
@exigir_acesso_erp
def relatorio_status_view(request, pk, status):
    try:
        usuario_historico = request.user if request.user.is_authenticated else None
        relatorio_atual = get_object_or_404(
            _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
            pk=pk,
        )
        if status == StatusRelatorio.CONFERENCIA:
            if not usuario_pode_enviar_relatorio(request.user, relatorio_atual):
                messages.error(request, "VocÃª nÃ£o tem permissÃ£o para enviar este relatÃ³rio.")
                return redirect("relatorios:relatorio_detail", pk=pk)
            relatorio = enviar_para_conferencia(pk, usuario_historico)
        elif status in {
            StatusRelatorio.AJUSTE,
            StatusRelatorio.REJEITADO,
            StatusRelatorio.APROVADO,
        } and not usuario_pode_atuar_como_financeiro(request.user):
            messages.error(request, "Você não tem permissão para executar esta ação financeira.")
            return redirect("relatorios:relatorio_detail", pk=pk)
        elif status == StatusRelatorio.AJUSTE:
            relatorio = solicitar_ajuste(
                pk,
                usuario_historico,
                request.POST.get("motivo_rejeicao", ""),
            )
        elif status == StatusRelatorio.REJEITADO:
            relatorio = rejeitar_relatorio(
                pk,
                usuario_historico,
                request.POST.get("motivo_rejeicao", ""),
            )
        elif status == StatusRelatorio.APROVADO:
            relatorio = aprovar_relatorio(pk, usuario_historico, request.POST)
        else:
            messages.error(request, "Status inválido.")
            return redirect("relatorios:relatorio_detail", pk=pk)
    except WorkflowError as exc:
        erros = _lista_erros_operacionais(exc)
        if _request_espera_json(request):
            return JsonResponse({"success": False, "errors": erros}, status=400)
        _adicionar_erros_operacionais(request, erros)
        return redirect("relatorios:relatorio_detail", pk=pk)
    except RelatorioTecnico.DoesNotExist:
        messages.error(request, "Relatório não encontrado.")
        return redirect("relatorios:relatorio_list")
    except Exception as exc:
        logger.exception("Erro ao alterar status do relatório %s: %s", pk, exc)
        messages.error(request, "Erro interno ao alterar status. Tente novamente.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    messages.success(
        request,
        f'Status alterado para "{relatorio.get_status_display()}".',
    )
    return redirect("relatorios:relatorio_detail", pk=pk)


# ─────────────────────────────────────────────
# TÉCNICOS
# ─────────────────────────────────────────────


class TecnicoListView(AdministrativoMixin, ListView):
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


@login_required
@exigir_administrativo
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


@login_required
@exigir_administrativo
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


class ClienteListView(AdministrativoMixin, ListView):
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


@login_required
@exigir_administrativo
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


@login_required
@exigir_administrativo
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


class AdiantamentoListView(AdministrativoMixin, ListView):
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


@login_required
@exigir_administrativo
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


@login_required
@exigir_administrativo
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
