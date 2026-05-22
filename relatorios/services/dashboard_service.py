from collections import defaultdict
from decimal import Decimal

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
    inicio_padrao = hoje.replace(day=1)
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


def get_kpis(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
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


def get_evolucao_financeira(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
    buckets = defaultdict(lambda: {"solicitado": Decimal("0.00"), "aprovado": Decimal("0.00")})
    for relatorio in relatorios:
        chave = _periodo_label(relatorio.data_inicio)
        buckets[chave]["solicitado"] += relatorio.total_solicitado
        buckets[chave]["aprovado"] += relatorio.total_aprovado

    labels = sorted(buckets)
    solicitado = [_decimal_json(buckets[label]["solicitado"]) for label in labels]
    aprovado = [_decimal_json(buckets[label]["aprovado"]) for label in labels]
    diferenca = [
        _decimal_json(max(buckets[label]["solicitado"] - buckets[label]["aprovado"], Decimal("0.00")))
        for label in labels
    ]
    return {
        "labels": labels,
        "series": [
            {"name": "Solicitado", "data": solicitado},
            {"name": "Aprovado", "data": aprovado},
            {"name": "Diferença removida", "data": diferenca},
        ],
    }


def get_gastos_por_cliente(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
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


def get_relatorios_por_tecnico(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
    totais = defaultdict(int)
    for relatorio in relatorios:
        tecnico = relatorio.tecnico_principal_exibicao()
        nome = tecnico.nome if tecnico else "Não informado"
        totais[nome] += 1
    ranking = sorted(totais.items(), key=lambda item: item[1], reverse=True)[:12]
    return {
        "labels": [nome for nome, _total in ranking],
        "series": [total for _nome, total in ranking],
    }


def get_km_por_tecnico(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
    totais = defaultdict(Decimal)
    for relatorio in relatorios:
        tecnico = relatorio.tecnico_principal_exibicao()
        nome = tecnico.nome if tecnico else "Não informado"
        totais[nome] += _money(relatorio.total_km_percorrido)
    ranking = sorted(totais.items(), key=lambda item: item[1], reverse=True)[:12]
    return {
        "labels": [nome for nome, _total in ranking],
        "series": [_decimal_json(total) for _nome, total in ranking],
    }


def get_status_relatorios(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
    labels = dict(StatusRelatorio.choices)
    contadores = {status: 0 for status in labels}
    for relatorio in relatorios:
        contadores[relatorio.status] = contadores.get(relatorio.status, 0) + 1
    return {
        "labels": [labels[status] for status in contadores],
        "series": [contadores[status] for status in contadores],
        "colors": [STATUS_CORES.get(status, "#6c757d") for status in contadores],
    }


def get_dashboard_data(user, params):
    filtros = filtros_dashboard(user, params)
    global_view = usuario_tem_dashboard_global(user)
    kpis = get_kpis(user, filtros)
    charts = {
        "evolucao_financeira": get_evolucao_financeira(user, filtros),
        "gastos_por_cliente": get_gastos_por_cliente(user, filtros),
        "status_relatorios": get_status_relatorios(user, filtros),
    }
    if global_view:
        charts["relatorios_por_tecnico"] = get_relatorios_por_tecnico(user, filtros)
        charts["km_por_tecnico"] = get_km_por_tecnico(user, filtros)
    else:
        charts["relatorios_por_tecnico"] = get_status_relatorios(user, filtros)
        charts["km_por_tecnico"] = get_evolucao_km_individual(user, filtros)

    return {
        "escopo": "global" if global_view else "individual",
        "filtros": serializar_filtros(filtros),
        "kpis": serializar_kpis(kpis),
        "charts": charts,
    }


def get_evolucao_km_individual(user, filtros):
    relatorios = _relatorios_materializados(user, filtros)
    buckets = defaultdict(Decimal)
    for relatorio in relatorios:
        buckets[_periodo_label(relatorio.data_inicio)] += _money(relatorio.total_km_percorrido)
    labels = sorted(buckets)
    return {
        "labels": labels,
        "series": [{"name": "KM", "data": [_decimal_json(buckets[label]) for label in labels]}],
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
