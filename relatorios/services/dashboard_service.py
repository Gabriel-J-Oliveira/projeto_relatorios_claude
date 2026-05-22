import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from relatorios.models import (
    Cliente,
    RelatorioTecnico,
    StatusRelatorio,
    Tecnico,
)
from relatorios.services.autorizacao_service import (
    queryset_relatorios_visiveis,
    usuario_eh_administrativo,
)

logger = logging.getLogger(__name__)

STATUS_CORES = {
    StatusRelatorio.RASCUNHO: "#6c757d",
    StatusRelatorio.CONFERENCIA: "#f0ad4e",
    StatusRelatorio.AJUSTE: "#fd7e14",
    StatusRelatorio.APROVADO: "#198754",
    StatusRelatorio.REJEITADO: "#dc3545",
}


def _money(valor):
    return Decimal(valor or "0.00").quantize(Decimal("0.01"))


def _decimal_json(valor):
    return float(_money(valor))


def _moeda(valor):
    valor = _money(valor)
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _periodo_label(data):
    if not data:
        return "Sem data"
    return data.strftime("%m/%Y")


def _usar_bucket_diario(filtros):
    data_inicio = filtros.get("data_inicio")
    data_fim = filtros.get("data_fim")
    if not data_inicio or not data_fim:
        return False
    return (data_fim - data_inicio).days <= 120


def _bucket_periodo(data, filtros):
    if not data:
        return "sem-data", "Sem data"
    if _usar_bucket_diario(filtros):
        return data.isoformat(), data.strftime("%d/%m")
    return data.strftime("%Y-%m"), data.strftime("%m/%Y")


def _parse_date(valor):
    if not valor:
        return None
    try:
        return timezone.datetime.fromisoformat(str(valor)).date()
    except ValueError:
        return None


def usuario_tem_dashboard_global(user):
    return usuario_eh_administrativo(user)


def filtros_dashboard(user, params):
    hoje = timezone.localdate()
    inicio_padrao = hoje - timedelta(days=365)
    status = (params.get("status") or "").strip()
    centro_custo = (params.get("centro_custo") or "").strip()

    filtros = {
        "data_inicio": _parse_date(params.get("data_inicio")) or inicio_padrao,
        "data_fim": _parse_date(params.get("data_fim")) or hoje,
        "cliente": None,
        "tecnico": None,
        "status": status,
        "centro_custo": centro_custo,
    }

    try:
        filtros["cliente"] = int(params.get("cliente") or 0) or None
    except (TypeError, ValueError):
        filtros["cliente"] = None

    if usuario_tem_dashboard_global(user):
        try:
            filtros["tecnico"] = int(params.get("tecnico") or 0) or None
        except (TypeError, ValueError):
            filtros["tecnico"] = None

    status_validos = {choice[0] for choice in StatusRelatorio.choices}
    if filtros["status"] not in status_validos:
        filtros["status"] = ""

    if filtros["data_fim"] and filtros["data_inicio"] and filtros["data_fim"] < filtros["data_inicio"]:
        filtros["data_inicio"], filtros["data_fim"] = filtros["data_fim"], filtros["data_inicio"]

    return filtros


def get_dashboard_queryset(user, filtros):
    qs = queryset_relatorios_visiveis(
        user,
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
            "criado_por",
        ).prefetch_related(
            "clientes_vinculados__cliente",
            "equipe__tecnico",
            "despesas__clientes_vinculados__cliente",
            "despesas__rateios__cliente",
            "trechos__clientes_vinculados__cliente",
            "trechos__rateios__cliente",
        ),
    )

    data_inicio = filtros.get("data_inicio")
    data_fim = filtros.get("data_fim")
    if data_inicio:
        qs = qs.filter(data_inicio__gte=data_inicio)
    if data_fim:
        qs = qs.filter(data_inicio__lte=data_fim)
    if filtros.get("status"):
        qs = qs.filter(status=filtros["status"])
    if filtros.get("centro_custo"):
        qs = qs.filter(centro_custo__icontains=filtros["centro_custo"])
    if filtros.get("cliente"):
        cliente_id = filtros["cliente"]
        qs = qs.filter(
            Q(cliente_id=cliente_id)
            | Q(clientes_vinculados__cliente_id=cliente_id)
        )
    if usuario_tem_dashboard_global(user) and filtros.get("tecnico"):
        tecnico_id = filtros["tecnico"]
        qs = qs.filter(
            Q(tecnico_responsavel_id=tecnico_id)
            | Q(equipe__tecnico_id=tecnico_id)
        )

    return qs.distinct()


def _relatorios_materializados(user, filtros):
    return list(get_dashboard_queryset(user, filtros))


def get_kpis(relatorios):
    total_solicitado = sum((r.total_solicitado for r in relatorios), Decimal("0.00"))
    total_aprovado = sum((r.total_aprovado for r in relatorios), Decimal("0.00"))
    km_total = sum((r.total_km_percorrido for r in relatorios), Decimal("0.00"))
    pendentes = sum(1 for r in relatorios if r.status == StatusRelatorio.CONFERENCIA)
    ajustes = sum(1 for r in relatorios if r.status == StatusRelatorio.AJUSTE)
    diferenca = total_solicitado - total_aprovado
    if diferenca < 0:
        diferenca = Decimal("0.00")

    return {
        "total_solicitado": total_solicitado,
        "total_aprovado": total_aprovado,
        "diferenca_removida": diferenca,
        "relatorios_pendentes": pendentes,
        "relatorios_ajuste": ajustes,
        "km_total": km_total,
        "total_relatorios": len(relatorios),
    }


def get_evolucao_financeira(relatorios, filtros):
    buckets = defaultdict(
        lambda: {
            "label": "",
            "solicitado": Decimal("0.00"),
            "aprovado": Decimal("0.00"),
        }
    )
    for relatorio in relatorios:
        chave, label = _bucket_periodo(relatorio.data_inicio, filtros)
        buckets[chave]["label"] = label
        buckets[chave]["solicitado"] += relatorio.total_solicitado
        buckets[chave]["aprovado"] += relatorio.total_aprovado

    chaves = sorted(buckets)
    labels = [buckets[chave]["label"] for chave in chaves]
    solicitado = [_decimal_json(buckets[chave]["solicitado"]) for chave in chaves]
    aprovado = [_decimal_json(buckets[chave]["aprovado"]) for chave in chaves]
    diferenca = [
        _decimal_json(max(buckets[chave]["solicitado"] - buckets[chave]["aprovado"], Decimal("0.00")))
        for chave in chaves
    ]
    return {
        "labels": labels,
        "series": [
            {"name": "Solicitado", "data": solicitado},
            {"name": "Aprovado", "data": aprovado},
            {"name": "Diferença removida", "data": diferenca},
        ],
    }


def get_gastos_por_cliente(relatorios):
    totais = defaultdict(Decimal)
    for relatorio in relatorios:
        for despesa in relatorio.despesas.all():
            if despesa.rejeitado or despesa.status_financeiro == "rejeitado":
                continue
            rateios = list(despesa.rateios.all())
            if rateios:
                for rateio in rateios:
                    totais[rateio.cliente.nome] += _money(rateio.valor_final)
            else:
                for cliente in despesa.clientes_vinculados.all():
                    totais[cliente.cliente.nome] += _money(despesa.valor_final)
        for trecho in relatorio.trechos.all():
            if trecho.rejeitado or trecho.status_financeiro == "rejeitado":
                continue
            for rateio in trecho.rateios.all():
                totais[rateio.cliente.nome] += _money(rateio.valor_final)
        for linha in relatorio.rateio_km_excedente_clientes():
            totais[linha["cliente"].nome] += _money(linha["valor_calculado"])

    ranking = sorted(totais.items(), key=lambda item: item[1], reverse=True)[:10]
    return {
        "labels": [nome for nome, _total in ranking],
        "series": [_decimal_json(total) for _nome, total in ranking],
    }


def get_relatorios_por_tecnico(relatorios):
    totais = defaultdict(int)
    for relatorio in relatorios:
        tecnicos = list(relatorio.tecnicos_exibicao())
        if not tecnicos:
            totais["Não informado"] += 1
            continue
        for tecnico in tecnicos:
            totais[tecnico.nome] += 1
    ranking = sorted(totais.items(), key=lambda item: item[1], reverse=True)[:12]
    return {
        "labels": [nome for nome, _total in ranking],
        "series": [total for _nome, total in ranking],
    }


def get_km_por_tecnico(relatorios):
    totais = defaultdict(Decimal)
    for relatorio in relatorios:
        tecnicos = list(relatorio.tecnicos_exibicao())
        if not tecnicos:
            totais["Não informado"] += _money(relatorio.total_km_percorrido)
            continue
        for tecnico in tecnicos:
            totais[tecnico.nome] += _money(relatorio.total_km_percorrido)
    ranking = sorted(totais.items(), key=lambda item: item[1], reverse=True)[:12]
    return {
        "labels": [nome for nome, _total in ranking],
        "series": [_decimal_json(total) for _nome, total in ranking],
    }


def get_status_relatorios(relatorios):
    labels = dict(StatusRelatorio.choices)
    contadores = {status: 0 for status in labels}
    for relatorio in relatorios:
        contadores[relatorio.status] = contadores.get(relatorio.status, 0) + 1
    return {
        "labels": [labels[status] for status in contadores],
        "series": [contadores[status] for status in contadores],
        "colors": [STATUS_CORES.get(status, "#6c757d") for status in contadores],
    }


def _dashboard_cache_key(user, filtros):
    payload = {
        "user": getattr(user, "pk", None),
        "global": usuario_tem_dashboard_global(user),
        "filtros": serializar_filtros(filtros),
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"dashboard:v2:{digest}"


def _dashboard_cache_ttl():
    return getattr(settings, "DASHBOARD_CACHE_TTL", 180)


def get_dashboard_data(user, params):
    filtros = filtros_dashboard(user, params)
    cache_key = _dashboard_cache_key(user, filtros)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    inicio = time.perf_counter()
    relatorios = _relatorios_materializados(user, filtros)
    global_view = usuario_tem_dashboard_global(user)
    kpis = get_kpis(relatorios)
    charts = {
        "evolucao_financeira": get_evolucao_financeira(relatorios, filtros),
        "gastos_por_cliente": get_gastos_por_cliente(relatorios),
        "status_relatorios": get_status_relatorios(relatorios),
    }
    if global_view:
        charts["relatorios_por_tecnico"] = get_relatorios_por_tecnico(relatorios)
        charts["km_por_tecnico"] = get_km_por_tecnico(relatorios)
    else:
        charts["relatorios_por_tecnico"] = get_status_relatorios(relatorios)
        charts["km_por_tecnico"] = get_evolucao_km_individual(relatorios, filtros)

    dados = {
        "escopo": "global" if global_view else "individual",
        "filtros": serializar_filtros(filtros),
        "kpis": serializar_kpis(kpis),
        "charts": charts,
    }
    cache.set(cache_key, dados, _dashboard_cache_ttl())
    duracao = time.perf_counter() - inicio
    if duracao > 2:
        logger.warning(
            "Dashboard calculado lentamente para usuario=%s escopo=%s filtros=%s duracao=%.2fs",
            getattr(user, "pk", None),
            dados["escopo"],
            dados["filtros"],
            duracao,
        )
    return dados


def get_evolucao_km_individual(relatorios, filtros):
    buckets = defaultdict(lambda: {"label": "", "km": Decimal("0.00")})
    for relatorio in relatorios:
        chave, label = _bucket_periodo(relatorio.data_inicio, filtros)
        buckets[chave]["label"] = label
        buckets[chave]["km"] += _money(relatorio.total_km_percorrido)
    chaves = sorted(buckets)
    labels = [buckets[chave]["label"] for chave in chaves]
    return {
        "labels": labels,
        "series": [{"name": "KM", "data": [_decimal_json(buckets[chave]["km"]) for chave in chaves]}],
    }


def serializar_kpis(kpis):
    return {
        "total_solicitado": _decimal_json(kpis["total_solicitado"]),
        "total_solicitado_formatado": _moeda(kpis["total_solicitado"]),
        "total_aprovado": _decimal_json(kpis["total_aprovado"]),
        "total_aprovado_formatado": _moeda(kpis["total_aprovado"]),
        "diferenca_removida": _decimal_json(kpis["diferenca_removida"]),
        "diferenca_removida_formatado": _moeda(kpis["diferenca_removida"]),
        "relatorios_pendentes": kpis["relatorios_pendentes"],
        "relatorios_ajuste": kpis["relatorios_ajuste"],
        "km_total": _decimal_json(kpis["km_total"]),
        "km_total_formatado": f"{kpis['km_total']:.2f}".replace(".", ","),
        "total_relatorios": kpis["total_relatorios"],
    }


def serializar_filtros(filtros):
    return {
        "data_inicio": filtros["data_inicio"].isoformat() if filtros.get("data_inicio") else "",
        "data_fim": filtros["data_fim"].isoformat() if filtros.get("data_fim") else "",
        "cliente": filtros.get("cliente") or "",
        "tecnico": filtros.get("tecnico") or "",
        "status": filtros.get("status") or "",
        "centro_custo": filtros.get("centro_custo") or "",
    }


def get_dashboard_context(user, params):
    dados = get_dashboard_data(user, params)
    filtros = dados["filtros"]
    qs_recentes = get_dashboard_queryset(user, filtros_dashboard(user, params)).order_by("-criado_em")[:8]
    pode_filtrar_tecnico = usuario_tem_dashboard_global(user)
    if pode_filtrar_tecnico:
        clientes_filtro = Cliente.objects.filter(ativo=True).order_by("nome")
    else:
        qs_visivel = queryset_relatorios_visiveis(user, RelatorioTecnico.objects.all())
        clientes_filtro = Cliente.objects.filter(
            Q(relatorios_cliente__relatorio__in=qs_visivel)
            | Q(relatorios__in=qs_visivel)
        ).filter(ativo=True).distinct().order_by("nome")
    return {
        "dashboard_data": dados,
        "dashboard_global": dados["escopo"] == "global",
        "dashboard_individual": dados["escopo"] != "global",
        "filtros_dashboard": filtros,
        "clientes_filtro": clientes_filtro,
        "tecnicos_filtro": Tecnico.objects.filter(ativo=True).order_by("nome") if pode_filtrar_tecnico else Tecnico.objects.none(),
        "status_filtro": StatusRelatorio.choices,
        "pode_filtrar_tecnico": pode_filtrar_tecnico,
        "relatorios_recentes": qs_recentes,
        "cards": dashboard_cards(dados["kpis"]),
    }


def dashboard_cards(kpis):
    return [
        {
            "id": "total_solicitado",
            "titulo": "Total solicitado",
            "valor": kpis["total_solicitado_formatado"],
            "icone": "bi-currency-dollar",
            "cor": "primary",
        },
        {
            "id": "total_aprovado",
            "titulo": "Total aprovado",
            "valor": kpis["total_aprovado_formatado"],
            "icone": "bi-check2-circle",
            "cor": "success",
        },
        {
            "id": "diferenca_removida",
            "titulo": "Diferença removida",
            "valor": kpis["diferenca_removida_formatado"],
            "icone": "bi-scissors",
            "cor": "danger",
        },
        {
            "id": "relatorios_pendentes",
            "titulo": "Conferência pendente",
            "valor": kpis["relatorios_pendentes"],
            "icone": "bi-hourglass-split",
            "cor": "warning",
        },
        {
            "id": "relatorios_ajuste",
            "titulo": "Em ajuste",
            "valor": kpis["relatorios_ajuste"],
            "icone": "bi-arrow-repeat",
            "cor": "orange",
        },
        {
            "id": "km_total",
            "titulo": "KM total",
            "valor": f"{kpis['km_total_formatado']} km",
            "icone": "bi-speedometer2",
            "cor": "info",
        },
    ]
