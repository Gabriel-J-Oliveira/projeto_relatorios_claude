import logging
import json
import time
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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView, TemplateView

from .models import (
    Adiantamento,
    AnexoRelatorio,
    Cliente,
    ItemDespesa,
    PerfilUsuario,
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
    obter_motivos_clientes_relatorio,
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
from .services.consulta_relatorio_service import montar_consulta_relatorio
from .services.financeiro_validator import validar_integridade_financeira_relatorio
from .services.pdf_cliente_service import (
    PdfClienteError,
    gerar_pdf_cliente,
    gerar_zip_pdfs_clientes,
    nome_arquivo_pdf_cliente,
)
from .services.pdf_interno_service import montar_contexto_pdf_interno
from .services.maps_service import MapsServiceError, buscar_endereco, calcular_rota
from .services.dashboard_service import get_dashboard_context, get_dashboard_data
from .forms import (
    AdiantamentoForm,
    ClienteForm,
    CompletarCadastroUsuarioForm,
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


@login_required
def completar_cadastro_view(request):
    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=request.user)
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = CompletarCadastroUsuarioForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.first_name = form.cleaned_data["first_name"].strip()
            request.user.last_name = form.cleaned_data["last_name"].strip()
            request.user.email = form.cleaned_data["email"]
            request.user.save(update_fields=["first_name", "last_name", "email"])
            perfil.cadastro_confirmado_em = timezone.now()
            perfil.save(update_fields=["cadastro_confirmado_em", "atualizado_em"])
            messages.success(request, "Dados cadastrais confirmados com sucesso.")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("relatorios:dashboard")
    else:
        form = CompletarCadastroUsuarioForm(user=request.user)

    return render(
        request,
        "registration/completar_cadastro.html",
        {
            "form": form,
            "next": next_url,
            "perfil": perfil,
        },
    )


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


def _motivos_clientes_do_request(request, instance=None):
    motivos = {}
    if request.method == "POST":
        for cliente_id in normalizar_ids_clientes(request.POST.get("clientes_relatorio")):
            motivos[cliente_id] = (
                request.POST.get(f"motivo_cliente_{cliente_id}") or ""
            ).strip()
        return motivos
    return obter_motivos_clientes_relatorio(instance) if instance else {}


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


def _clientes_item_instance_value(instance):
    if not getattr(instance, "pk", None):
        return ""
    try:
        ids = instance.clientes_vinculados.values_list("cliente_id", flat=True)
    except Exception:
        return ""
    return ",".join(str(cliente_id) for cliente_id in ids)


def _popular_clientes_formset_para_template(formset, request):
    for form in getattr(formset, "forms", []):
        chave = f"{form.prefix}-clientes"
        if request.method == "POST":
            valor = request.POST.get(chave, "")
        else:
            valor = _clientes_item_instance_value(form.instance)
        form.clientes_value = valor
        form.clientes_value_set = True


def _popular_clientes_formsets_para_template(request, fs_desp, fs_km):
    _popular_clientes_formset_para_template(fs_desp, request)
    _popular_clientes_formset_para_template(fs_km, request)


def _registrar_metadados_comprovante(relatorio, usuario, item, arquivo_original=None):
    arquivo = getattr(item, "comprovante", None)
    if not arquivo:
        return
    if isinstance(item, ItemDespesa):
        AnexoRelatorio.registrar_comprovante(
            relatorio=relatorio,
            usuario=usuario,
            despesa=item,
            arquivo=arquivo,
            arquivo_original=arquivo_original,
        )


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
        tipo_relatorio=original.tipo_relatorio,
        valor_adiantamento=original.valor_adiantamento or Decimal("0.00"),
        km_excedente_interno=original.km_excedente_interno or Decimal("0.00"),
        observacao_km_excedente=original.observacao_km_excedente,
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

    clientes_originais = list(original.clientes_vinculados.select_related("cliente").all())
    if not clientes_originais and original.cliente_id:
        novo.clientes_vinculados.create(
            cliente=original.cliente,
            ordem=1,
            motivo_viagem=original.motivo or "",
        )
    for vinculo in clientes_originais:
        novo.clientes_vinculados.create(
            cliente=vinculo.cliente,
            ordem=vinculo.ordem,
            motivo_viagem=vinculo.motivo_viagem or original.motivo or "",
        )

    despesas_originais = list(
        original.despesas.prefetch_related("clientes_vinculados__cliente").all()
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
        for despesa in despesas_originais
    ]
    if despesas:
        despesas_criadas = ItemDespesa.objects.bulk_create(despesas)
        for despesa_original, despesa_nova in zip(despesas_originais, despesas_criadas):
            vinculos_item = list(despesa_original.clientes_vinculados.all())
            if not vinculos_item and original.cliente_id:
                despesa_nova.clientes_vinculados.create(cliente=original.cliente)
            for vinculo in vinculos_item:
                despesa_nova.clientes_vinculados.create(cliente=vinculo.cliente)

    trechos_originais = list(
        original.trechos.prefetch_related("clientes_vinculados__cliente").all()
    )
    trechos = [
        TrechoKm(
            relatorio=novo,
            ordem=trecho.ordem,
            data=None,
            origem=trecho.origem,
            origem_endereco_completo=trecho.origem_endereco_completo,
            origem_lat=trecho.origem_lat,
            origem_lon=trecho.origem_lon,
            destino=trecho.destino,
            destino_endereco_completo=trecho.destino_endereco_completo,
            destino_lat=trecho.destino_lat,
            destino_lon=trecho.destino_lon,
            km=trecho.km,
            km_calculado_api=trecho.km_calculado_api,
            km_informado=trecho.km_informado,
            diferenca_km_percentual=trecho.diferenca_km_percentual,
            fonte_calculo_rota=trecho.fonte_calculo_rota,
            calculado_em=trecho.calculado_em,
            rota_geojson=trecho.rota_geojson or {},
            valor_km=trecho.valor_km,
            valor_km_aprovado=None,
            comprovante=None,
            observacao=trecho.observacao,
        )
        for trecho in trechos_originais
    ]
    for trecho in trechos:
        trecho.valor_calculado = (trecho.km * trecho.valor_km).quantize(
            Decimal("0.01")
        )
    if trechos:
        trechos_criados = TrechoKm.objects.bulk_create(trechos)
        for trecho_original, trecho_novo in zip(trechos_originais, trechos_criados):
            vinculos_item = list(trecho_original.clientes_vinculados.all())
            if not vinculos_item and original.cliente_id:
                trecho_novo.clientes_vinculados.create(cliente=original.cliente)
            for vinculo in vinculos_item:
                trecho_novo.clientes_vinculados.create(cliente=vinculo.cliente)

    return novo


def _snapshot_geo_trecho(trecho):
    if not trecho:
        return None
    return {
        "km": trecho.km,
        "km_calculado_api": trecho.km_calculado_api,
        "km_informado": trecho.km_informado,
        "diferenca_km_percentual": trecho.diferenca_km_percentual,
        "fonte_calculo_rota": trecho.fonte_calculo_rota,
        "rota_geojson": trecho.rota_geojson or {},
    }


def _snapshot_km_excedente(relatorio):
    if not relatorio:
        return None
    return {
        "km": relatorio.km_excedente_interno or Decimal("0.00"),
        "observacao": relatorio.observacao_km_excedente or "",
    }


def _registrar_auditoria_km_excedente(relatorio, usuario, anterior=None):
    anterior = anterior or {"km": Decimal("0.00"), "observacao": ""}
    km_anterior = anterior.get("km") or Decimal("0.00")
    km_atual = relatorio.km_excedente_interno or Decimal("0.00")
    obs_anterior = anterior.get("observacao") or ""
    obs_atual = relatorio.observacao_km_excedente or ""

    if km_anterior == km_atual and obs_anterior == obs_atual:
        return

    if km_anterior <= 0 and km_atual > 0:
        descricao = "KM excedente / deslocamento interno criado."
    elif km_anterior > 0 and km_atual <= 0:
        descricao = "KM excedente / deslocamento interno removido."
    else:
        descricao = "KM excedente / deslocamento interno alterado."

    registrar_evento(
        relatorio,
        usuario,
        TipoEventoHistorico.VALOR_ALTERADO,
        descricao,
        {
            "valor_anterior": str(km_anterior),
            "valor_novo": str(km_atual),
            "observacao_anterior": obs_anterior,
            "observacao_nova": obs_atual,
            "clientes_impactados": [
                {
                    "cliente_id": linha["cliente"].pk,
                    "cliente_nome": linha["cliente"].nome,
                    "km": str(linha["km"]),
                    "valor_km": str(linha["valor_km"]),
                    "valor_calculado": str(linha["valor_calculado"]),
                }
                for linha in relatorio.rateio_km_excedente_clientes()
            ],
        },
    )


def _registrar_auditoria_geografica_trecho(relatorio, usuario, trecho, anterior=None):
    dados = {
        "trecho_id": trecho.pk,
        "origem": trecho.origem,
        "destino": trecho.destino,
        "origem_endereco_completo": trecho.origem_endereco_completo,
        "destino_endereco_completo": trecho.destino_endereco_completo,
        "km_calculado_api": str(trecho.km_calculado_api or ""),
        "km_informado": str(trecho.km_informado or trecho.km or ""),
        "diferenca_km_percentual": str(trecho.diferenca_km_percentual or ""),
        "fonte_calculo_rota": trecho.fonte_calculo_rota or "",
        "anterior": {
            chave: str(valor or "")
            for chave, valor in (anterior or {}).items()
        },
    }
    diferenca_anterior = (anterior or {}).get("diferenca_km_percentual") or Decimal("0.00")

    if trecho.km_calculado_api and not (anterior or {}).get("km_calculado_api"):
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "Rota de KM calculada automaticamente.",
            dados,
        )

    if trecho.km_calculado_api and anterior and anterior.get("km") != trecho.km:
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado alterado manualmente após cálculo de rota.",
            dados,
        )

    if trecho.km_divergente_rota and diferenca_anterior <= Decimal("15.00"):
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado difere mais de 15% da rota calculada.",
            dados,
        )

    if not trecho.km_calculado_api and trecho.km and not anterior:
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado manualmente sem rota calculada.",
            dados,
        )


def _mapa_trechos_relatorio(relatorio):
    dados = []
    for ordem, trecho in enumerate(relatorio.trechos.all(), start=1):
        if not all([trecho.origem_lat, trecho.origem_lon, trecho.destino_lat, trecho.destino_lon]):
            continue
        clientes = [rateio.cliente.nome for rateio in trecho.rateios.all()] or [
            vinculo.cliente.nome for vinculo in trecho.clientes_vinculados.all()
        ]
        dados.append(
            {
                "ordem": ordem,
                "origem": trecho.origem_endereco_completo or trecho.origem,
                "destino": trecho.destino_endereco_completo or trecho.destino,
                "origem_lat": str(trecho.origem_lat),
                "origem_lon": str(trecho.origem_lon),
                "destino_lat": str(trecho.destino_lat),
                "destino_lon": str(trecho.destino_lon),
                "rota_geojson": trecho.rota_geojson or {},
                "km_calculado": str(trecho.km_calculado_api or ""),
                "km_informado": str(trecho.km_informado or trecho.km or ""),
                "diferenca_percentual": str(trecho.diferenca_km_percentual or ""),
                "divergente": trecho.km_divergente_rota,
                "clientes": clientes,
            }
        )
    return dados


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
            messages.error(request, "Você não tem permissão para acessar esta área.")
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
    trechos_km_rota_divergente = []

    for idx, trecho in enumerate(trechos, start=1):
        linha_id = f"linha-trecho-{idx}"

        if trecho.data:
            trechos_por_data.setdefault(trecho.data, []).append((idx, trecho))

        if trecho.km_divergente_rota:
            trechos_km_rota_divergente.append((idx, trecho))

    if trechos_km_rota_divergente:
        linha_ids = [
            f"linha-trecho-{idx}" for idx, _trecho in trechos_km_rota_divergente
        ]
        for idx, _trecho in trechos_km_rota_divergente:
            linha_id = f"linha-trecho-{idx}"
            avisos.append(
                {
                    "tipo": "km_rota_divergente",
                    "mensagem": "KM informado difere mais de 15% da rota calculada.",
                    "linha_id": linha_id,
                    "linha_ids": linha_ids,
                    "icone": "bi-signpost-split",
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
        ctx.update(get_dashboard_context(self.request.user, self.request.GET))
        ctx["titulo_pagina"] = "Dashboard"
        return ctx


@require_GET
@login_required
@exigir_acesso_erp
def dashboard_dados_json(request):
    inicio = time.perf_counter()
    dados = get_dashboard_data(request.user, request.GET)
    duracao = time.perf_counter() - inicio
    if duracao > 2:
        logger.warning(
            "Endpoint JSON do dashboard lento. usuario=%s duracao=%.2fs",
            request.user.pk,
            duracao,
        )
    return JsonResponse(dados)


class RelatorioListView(AcessoErpMixin, ListView):
    model = RelatorioTecnico
    template_name = "relatorios/relatorio_list.html"
    context_object_name = "relatorios"
    paginate_by = 15

    def get_queryset(self):
        qs = _relatorios_visiveis(
            self.request.user,
            RelatorioTecnico.objects.select_related(
                "cliente", "tecnico_responsavel", "aprovado_por", "criado_por", "snapshot_financeiro"
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "equipe__tecnico",
                "despesas",
                "trechos",
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
        sort = self.request.GET.get("sort")
        direction = self.request.GET.get("dir")
        if sort == "numero":
            campo_numero = "-numero" if direction == "desc" else "numero"
            return qs.order_by(campo_numero, "-criado_em")
        return qs.order_by("-criado_em")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        params_sem_ordem = params.copy()
        params_sem_ordem.pop("sort", None)
        params_sem_ordem.pop("dir", None)
        sort_atual = self.request.GET.get("sort")
        direcao_atual = self.request.GET.get("dir")
        proxima_direcao_numero = (
            "desc" if sort_atual == "numero" and direcao_atual != "desc" else "asc"
        )
        params_numero = params_sem_ordem.copy()
        params_numero["sort"] = "numero"
        params_numero["dir"] = proxima_direcao_numero
        ctx["form_filtro"] = _relatorio_filtro_form(self.request.user, self.request.GET)
        ctx["titulo_pagina"] = "Relatórios Técnicos"
        ctx["total"] = self.get_queryset().count()
        ctx["sort_atual"] = sort_atual
        ctx["direcao_atual"] = direcao_atual
        ctx["numero_sort_url"] = "?" + params_numero.urlencode()
        ctx["pagination_query"] = params.urlencode()
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
        motivos_clientes = _motivos_clientes_do_request(request, instance)
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
        motivos_clientes = _motivos_clientes_do_request(request, instance)
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
            request.FILES,
            instance=instance,
            prefix="trechos",
            form_kwargs={"valor_km_padrao": valor_km_padrao},
        )
        _popular_clientes_formsets_para_template(request, fs_desp, fs_km)

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
                        if not relatorio_novo:
                            relatorio_atual = RelatorioTecnico.objects.select_for_update().get(
                                pk=instance.pk
                            )
                            if not _relatorio_editavel_por_usuario(relatorio_atual, request.user):
                                raise WorkflowError(
                                    "Este relatório foi alterado por outro usuário e não pode mais ser editado."
                                )
                            km_excedente_anterior = _snapshot_km_excedente(relatorio_atual)
                            relatorio.status = relatorio_atual.status
                        else:
                            km_excedente_anterior = _snapshot_km_excedente(None)
                        if relatorio_novo:
                            relatorio.criado_por = request.user
                        relatorio.cliente_id = cliente_ids_relatorio[0]
                        relatorio.save()
                        form.save_m2m()
                        sync_clientes_relatorio(
                            relatorio,
                            cliente_ids_relatorio,
                            motivos_clientes,
                        )

                        tecnicos_apoio = form.cleaned_data.get("tecnicos_equipe", [])
                        _sync_equipe(relatorio, tecnicos_apoio)
                        usuario_historico = (
                            request.user if request.user.is_authenticated else None
                        )
                        _registrar_auditoria_km_excedente(
                            relatorio,
                            usuario_historico,
                            km_excedente_anterior,
                        )

                        fs_desp.instance = relatorio
                        for f in fs_desp.forms:
                            if not _form_has_content(f):
                                continue
                            item = f.save(commit=False)
                            item.relatorio = relatorio
                            item.save()
                            _registrar_metadados_comprovante(
                                relatorio,
                                usuario_historico,
                                item,
                                f.cleaned_data.get("comprovante"),
                            )
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
                            trecho_anterior = (
                                _snapshot_geo_trecho(
                                    TrechoKm.objects.select_for_update().get(pk=f.instance.pk)
                                )
                                if f.instance.pk
                                else None
                            )
                            trecho = f.save(commit=False)
                            trecho.relatorio = relatorio
                            clientes_trecho = _clientes_item_post(request, f.prefix)
                            if len(clientes_trecho) == 1:
                                trecho.valor_km = Decimal(
                                    str(_get_valor_km_para_cliente(clientes_trecho[0]) or "0")
                                )
                            elif len(clientes_trecho) > 1:
                                trecho.valor_km = Decimal("0.00")
                            trecho.save()
                            _registrar_auditoria_geografica_trecho(
                                relatorio,
                                usuario_historico,
                                trecho,
                                trecho_anterior,
                            )
                            erros_trecho = sync_clientes_trecho(
                                trecho,
                                clientes_trecho,
                            )
                            if erros_trecho:
                                raise WorkflowError(erros_trecho)
                        for f in fs_km.deleted_forms:
                            if f.instance.pk:
                                f.instance.delete()

                        erros_integridade = validar_integridade_financeira_relatorio(
                            relatorio
                        )
                        if erros_integridade:
                            raise WorkflowError(erros_integridade)

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
                            "motivos_clientes_relatorio": motivos_clientes,
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
                            "motivos_clientes_relatorio": motivos_clientes,
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
        _popular_clientes_formsets_para_template(request, fs_desp, fs_km)

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
            "motivos_clientes_relatorio": motivos_clientes,
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
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return redirect("relatorios:relatorio_consulta", pk=relatorio.pk)

    inconsistencias_rateio = []
    try:
        garantir_rateios_relatorio(relatorio)
    except RateioError as exc:
        inconsistencias_rateio = [str(exc)]
    relatorio = (
        RelatorioTecnico.objects.select_related(
            "cliente", "tecnico_responsavel", "aprovado_por", "criado_por", "snapshot_financeiro"
        )
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
            "inconsistencias_rateio": inconsistencias_rateio,
            "distribuicao_clientes": distribuicao_clientes,
            "mapa_trechos_json": _mapa_trechos_relatorio(relatorio),
            "titulo_pagina": f"Relatório {relatorio.identificador}",
        },
    )


@login_required
@exigir_acesso_erp
def relatorio_consulta_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
                "aprovado_por",
                "criado_por",
                "snapshot_financeiro",
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
    if relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        messages.error(
            request,
            "A consulta final fica disponível apenas para relatórios aprovados ou rejeitados.",
        )
        return redirect("relatorios:relatorio_detail", pk=relatorio.pk)
    consulta = montar_consulta_relatorio(relatorio)

    return render(
        request,
        "relatorios/relatorio_consulta.html",
        {
            "relatorio": relatorio,
            "consulta": consulta,
            "distribuicao_clientes": consulta["distribuicao_clientes"],
            "mapa_trechos_json": consulta.get("mapa_trechos", []),
            "pode_gerar_pdf_interno": usuario_pode_atuar_como_financeiro(request.user),
            "titulo_pagina": f"Consulta {consulta['relatorio'].identificador}",
        },
    )


# ─────────────────────────────────────────────
# EXCLUIR
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def relatorio_reembolso_pdf_view(request, pk):
    return relatorio_clientes_pdf_view(request, pk)

    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF oficial só pode ser gerado após aprovação.")
        if relatorio.status == StatusRelatorio.REJEITADO:
            return redirect("relatorios:relatorio_consulta", pk=pk)
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
        return redirect("relatorios:relatorio_consulta", pk=pk)

    css_path = settings.BASE_DIR / "templates" / "pdf" / "relatorio_reembolso.css"
    pdf = HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-reembolso-{relatorio.numero}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def _relatorio_pdf_cliente_or_404(request, pk):
    return get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
                "snapshot_financeiro",
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "despesas__rateios__cliente",
                "trechos__clientes_vinculados__cliente",
                "trechos__rateios__cliente",
                "equipe__tecnico",
            ),
        ),
        pk=pk,
    )

def _redirect_pdf_cliente_error(relatorio):
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return redirect("relatorios:relatorio_consulta", pk=relatorio.pk)
    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)


@login_required
@exigir_acesso_erp
def relatorio_cliente_pdf_view(request, pk, cliente_id):
    relatorio = _relatorio_pdf_cliente_or_404(request, pk)
    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF do cliente só pode ser gerado após aprovação.")
        return _redirect_pdf_cliente_error(relatorio)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do PDF de cliente para relatorio %s cliente %s.", pk, cliente_id)
    try:
        pdf, contexto = gerar_pdf_cliente(relatorio, cliente_id, request=request)
    except PermissionDenied:
        raise
    except PdfClienteError as exc:
        logger.exception(
            "Erro ao gerar PDF do cliente %s no relatorio %s: %s",
            cliente_id,
            relatorio.pk,
            exc,
        )
        messages.error(request, str(exc))
        return _redirect_pdf_cliente_error(relatorio)

    filename = nome_arquivo_pdf_cliente(relatorio, contexto["cliente"])
    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 5:
        logger.warning("PDF de cliente lento. relatorio=%s cliente=%s duracao=%.2fs", pk, cliente_id, duracao_pdf)
    logger.info("PDF de cliente gerado para relatorio %s cliente %s.", pk, cliente_id)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
@exigir_acesso_erp
def relatorio_clientes_pdf_view(request, pk):
    relatorio = _relatorio_pdf_cliente_or_404(request, pk)
    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF do cliente só pode ser gerado após aprovação.")
        return _redirect_pdf_cliente_error(relatorio)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do ZIP de PDFs de clientes para relatorio %s.", pk)
    try:
        zip_bytes, gerados, ignorados = gerar_zip_pdfs_clientes(
            relatorio,
            request=request,
        )
    except PermissionDenied:
        raise
    except PdfClienteError as exc:
        logger.exception(
            "Erro ao gerar PDFs dos clientes do relatorio %s: %s",
            relatorio.pk,
            exc,
        )
        messages.error(request, str(exc))
        return _redirect_pdf_cliente_error(relatorio)

    if ignorados:
        logger.info(
            "PDFs de clientes gerados para relatorio %s com %s arquivo(s) e %s cliente(s) ignorado(s).",
            relatorio.pk,
            len(gerados),
            len(ignorados),
        )

    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 8:
        logger.warning("ZIP de PDFs de clientes lento. relatorio=%s duracao=%.2fs", pk, duracao_pdf)
    logger.info("ZIP de PDFs de clientes gerado para relatorio %s com %s arquivo(s).", pk, len(gerados))
    filename = f"relatorio_{relatorio.numero or relatorio.pk}_clientes.zip"
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@exigir_financeiro
def relatorio_pdf_interno_view(request, pk):
    relatorio = get_object_or_404(
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
            "aprovado_por",
            "snapshot_financeiro",
        ).prefetch_related(
            "clientes_vinculados__cliente",
            "despesas__clientes_vinculados__cliente",
            "despesas__rateios__cliente",
            "trechos__clientes_vinculados__cliente",
            "trechos__rateios__cliente",
            "equipe__tecnico",
            "historicos__usuario",
        ),
        pk=pk,
    )
    if relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        messages.error(
            request,
            "O PDF interno só pode ser gerado para relatórios finalizados.",
        )
        return redirect("relatorios:relatorio_detail", pk=pk)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do PDF interno do relatorio %s.", pk)
    emitido_em = timezone.localtime(timezone.now())
    usuario_gerador = request.user if request.user.is_authenticated else None
    pdf_contexto = montar_contexto_pdf_interno(
        relatorio,
        emitido_em,
        usuario_gerador=usuario_gerador,
        avisos_financeiro=_avisos_financeiro(relatorio),
    )

    html = render_to_string(
        "relatorios/pdf/interno.html",
        {
            "pdf": pdf_contexto,
            "empresa": "CONTROL SUL GESTÃO EMPRESARIAL",
            "emitido_em": emitido_em,
            "usuario_gerador": usuario_gerador,
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
        return redirect("relatorios:relatorio_consulta", pk=pk)

    css_path = settings.BASE_DIR / "static" / "css" / "pdf-relatorio.css"
    pdf = HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-interno-{relatorio.numero}.pdf"
    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 5:
        logger.warning("PDF interno lento. relatorio=%s duracao=%.2fs", pk, duracao_pdf)
    logger.info("PDF interno gerado para relatorio %s.", pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


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
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "trechos__clientes_vinculados__cliente",
            ),
        ),
        pk=pk,
    )
    clientes_relatorio = obter_clientes_relatorio(relatorio)
    motivos_clientes = obter_motivos_clientes_relatorio(relatorio)

    return JsonResponse(
        {
            "id": relatorio.pk,
            "numero": relatorio.identificador,
            "cliente_id": relatorio.cliente_id,
            "cliente_ids": [cliente.pk for cliente in clientes_relatorio],
            "motivos_clientes": motivos_clientes,
            "tecnico_id": relatorio.tecnico_responsavel_id,
            "apoio_ids": list(relatorio.equipe.values_list("tecnico_id", flat=True)),
            "valor_adiantamento": str(relatorio.valor_adiantamento or Decimal("0.00")),
            "km_excedente_interno": str(relatorio.km_excedente_interno or Decimal("0.00")),
            "observacao_km_excedente": relatorio.observacao_km_excedente or "",
            "despesas": [
                {
                    "tipo": despesa.tipo,
                    "descricao": despesa.descricao,
                    "valor": str(despesa.valor),
                    "observacoes": despesa.observacoes,
                    "cliente_ids": list(
                        despesa.clientes_vinculados.values_list("cliente_id", flat=True)
                    ),
                }
                for despesa in relatorio.despesas.all()
            ],
            "trechos": [
                {
                    "origem": trecho.origem,
                    "origem_endereco_completo": trecho.origem_endereco_completo,
                    "origem_lat": str(trecho.origem_lat or ""),
                    "origem_lon": str(trecho.origem_lon or ""),
                    "destino": trecho.destino,
                    "destino_endereco_completo": trecho.destino_endereco_completo,
                    "destino_lat": str(trecho.destino_lat or ""),
                    "destino_lon": str(trecho.destino_lon or ""),
                    "km": str(trecho.km),
                    "km_calculado_api": str(trecho.km_calculado_api or ""),
                    "km_informado": str(trecho.km_informado or trecho.km or ""),
                    "diferenca_km_percentual": str(trecho.diferenca_km_percentual or ""),
                    "fonte_calculo_rota": trecho.fonte_calculo_rota or "",
                    "rota_geojson": trecho.rota_geojson or {},
                    "valor_km": str(trecho.valor_km),
                    "cliente_ids": list(
                        trecho.clientes_vinculados.values_list("cliente_id", flat=True)
                    ),
                }
                for trecho in relatorio.trechos.all()
            ],
        }
    )


@login_required
@require_GET
def mapa_buscar_endereco_json(request):
    query = (request.GET.get("q") or "").strip()
    if not query:
        return JsonResponse(
            {"success": False, "error": "Informe um endereço para buscar."},
            status=400,
        )

    inicio = time.perf_counter()
    try:
        resultados = buscar_endereco(query)
    except MapsServiceError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Erro inesperado ao buscar endereço: %s", exc)
        return JsonResponse(
            {"success": False, "error": "Erro interno ao buscar endereço."},
            status=500,
        )

    duracao = time.perf_counter() - inicio
    if duracao > 3:
        logger.warning("Busca de endereco lenta. duracao=%.2fs tamanho_query=%s", duracao, len(query))
    return JsonResponse({"success": True, "data": resultados})


@login_required
@require_GET
def mapa_calcular_rota_json(request):
    campos = ("origem_lat", "origem_lon", "destino_lat", "destino_lon")
    parametros = {campo: request.GET.get(campo) for campo in campos}
    faltando = [
        campo
        for campo, valor in parametros.items()
        if not str(valor or "").strip()
    ]
    if faltando:
        return JsonResponse(
            {
                "success": False,
                "error": "Informe origem e destino completos para calcular a rota.",
            },
            status=400,
        )

    inicio = time.perf_counter()
    try:
        rota = calcular_rota(**parametros)
    except MapsServiceError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Erro inesperado ao calcular rota: %s", exc)
        return JsonResponse(
            {"success": False, "error": "Erro interno ao calcular rota."},
            status=500,
        )

    duracao = time.perf_counter() - inicio
    if duracao > 3:
        logger.warning("Calculo de rota lento. duracao=%.2fs", duracao)
    return JsonResponse({"success": True, "data": rota})


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
        logger.exception("Erro ao duplicar relatório %s: %s", pk, exc)
        messages.error(request, "Erro interno ao duplicar relatório. Tente novamente.")
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
    espera_json = _request_espera_json(request)
    def resposta_erro(mensagem, status=400):
        if espera_json:
            return JsonResponse({"success": False, "errors": [mensagem]}, status=status)
        messages.error(request, mensagem)
        return redirect("relatorios:relatorio_detail", pk=pk)

    if tipo not in {"despesa", "trecho"} or acao not in {"rejeitar", "restaurar"}:
        return resposta_erro("Ação inválida para o item.")

    try:
        with transaction.atomic():
            relatorio = get_object_or_404(
                RelatorioTecnico.objects.select_for_update(), pk=pk
            )
            if _relatorio_bloqueado(relatorio):
                return resposta_erro(
                    "Relatório aprovado ou rejeitado está bloqueado para alterações.",
                )

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
                    return resposta_erro("Informe a justificativa da rejeição do item.")

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
                mensagem_sucesso = "Item removido do reembolso."

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
                mensagem_sucesso = "Item restaurado para o reembolso."

        if espera_json:
            return JsonResponse(
                {
                    "success": True,
                    "tipo": tipo,
                    "item_id": item.pk,
                    "acao": acao,
                    "rejeitado": bool(item.rejeitado),
                    "status_financeiro": item.status_financeiro,
                    "valor_final": str(
                        item.valor_final if tipo == "despesa" else item.valor_final_clientes
                    ),
                    "message": mensagem_sucesso,
                }
            )
        messages.success(request, mensagem_sucesso)

    except Exception as exc:
        logger.exception("Erro ao alterar item financeiro do relatório %s: %s", pk, exc)
        if espera_json:
            return JsonResponse(
                {
                    "success": False,
                    "errors": ["Erro interno ao alterar item. Tente novamente."],
                },
                status=500,
            )
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
        with transaction.atomic():
            relatorio = get_object_or_404(
                _relatorios_visiveis(
                    request.user,
                    RelatorioTecnico.objects.select_for_update(),
                ),
                pk=pk,
            )
            if _relatorio_bloqueado(relatorio):
                return JsonResponse(
                    {"success": False, "errors": ["Relatorio aprovado ou rejeitado esta bloqueado."]},
                    status=400,
                )

            modelo = ItemDespesa if tipo == "despesa" else TrechoKm
            item = get_object_or_404(
                modelo.objects.select_for_update(),
                pk=item_pk,
                relatorio=relatorio,
            )
            if item.rejeitado or item.status_financeiro == StatusFinanceiroItem.REJEITADO:
                return JsonResponse(
                    {"success": False, "errors": ["Item rejeitado nao pode ter rateio alterado."]},
                    status=400,
                )

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
                messages.error(request, "Você não tem permissão para enviar este relatório.")
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
    if getattr(relatorio, "_email_warning", ""):
        messages.warning(request, relatorio._email_warning)
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
