from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Prefetch

from relatorios.models import (
    ItemDespesa,
    RelatorioTecnico,
    StatusFinanceiroItem,
    TrechoKm,
)
from relatorios.services.financeiro_validator import validar_integridade_financeira_relatorio
from relatorios.services.resumo_cliente_service import resumo_financeiro_por_cliente


CENTAVO = Decimal("0.01")


def _money(valor):
    return Decimal(valor or "0").quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _str_money(valor):
    return str(_money(valor))


def _ativo(item):
    return not (
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def carregar_relatorio_financeiro(relatorio_id):
    return (
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
            "aprovado_por",
            "criado_por",
        )
        .prefetch_related(
            "clientes_vinculados__cliente",
            "equipe__tecnico",
            Prefetch(
                "despesas",
                queryset=ItemDespesa.objects.prefetch_related(
                    "clientes_vinculados__cliente",
                    "rateios__cliente",
                ).order_by("ordem", "data", "tipo"),
            ),
            Prefetch(
                "trechos",
                queryset=TrechoKm.objects.prefetch_related(
                    "clientes_vinculados__cliente",
                    "rateios__cliente",
                ).order_by("ordem", "data"),
            ),
            "historicos",
        )
        .get(pk=relatorio_id)
    )


def _serializar_despesa(despesa):
    return {
        "tipo": "despesa",
        "id": despesa.pk,
        "rejeitado": not _ativo(despesa),
        "status_financeiro": despesa.status_financeiro,
        "valor_solicitado": _str_money(despesa.valor),
        "valor_final": _str_money(despesa.valor_final),
        "valor_politica": _str_money(despesa.valor_politica)
        if despesa.valor_politica is not None
        else "",
        "excesso_politica": _str_money(despesa.excesso_politica),
        "acima_politica": bool(despesa.acima_politica),
        "rateios": [
            {
                "cliente_id": rateio.cliente_id,
                "cliente": rateio.cliente.nome,
                "valor_original": _str_money(rateio.valor_original),
                "valor_final": _str_money(rateio.valor_final),
                "status": rateio.status,
                "status_label": rateio.get_status_display(),
            }
            for rateio in despesa.rateios.all()
        ],
    }


def _serializar_trecho(trecho):
    return {
        "tipo": "trecho",
        "id": trecho.pk,
        "rejeitado": not _ativo(trecho),
        "status_financeiro": trecho.status_financeiro,
        "valor_solicitado": _str_money(trecho.valor_calculado_clientes),
        "valor_final": _str_money(trecho.valor_final_clientes),
        "rateios": [
            {
                "cliente_id": rateio.cliente_id,
                "cliente": rateio.cliente.nome,
                "km_cliente": str(rateio.km_cliente),
                "valor_km": str(rateio.valor_km),
                "valor_calculado": _str_money(rateio.valor_calculado),
                "valor_final": _str_money(rateio.valor_final),
                "valor_km_control_sul": str(rateio.valor_km_control_sul),
                "valor_reembolso_tecnico": _str_money(rateio.valor_reembolso_tecnico),
                "excesso_reducao": _str_money(rateio.excesso_reducao),
                "status": rateio.status,
                "status_label": rateio.get_status_display(),
            }
            for rateio in trecho.rateios.all()
        ],
    }


def _serializar_clientes(relatorio):
    distribuicao = resumo_financeiro_por_cliente(relatorio)
    return {
        "total": distribuicao["total"],
        "erros": distribuicao["erros"],
        "clientes": [
            {
                "cliente_id": resumo.cliente.pk,
                "cliente": resumo.cliente.nome,
                "motivo_viagem": resumo.motivo_viagem,
                "km_total": str(resumo.km_total),
                "valor_km_solicitado": _str_money(resumo.valor_km_solicitado),
                "valor_km_reembolso_tecnico": _str_money(resumo.valor_km_reembolso_tecnico),
                "excesso_reducao_km": _str_money(resumo.excesso_reducao_km),
                "despesas_solicitadas": _str_money(resumo.despesas_solicitadas),
                "total_solicitado": _str_money(resumo.total_solicitado),
                "total_aprovado": _str_money(resumo.total_aprovado),
                "diferenca_removida": _str_money(resumo.diferenca_removida),
                "itens_rejeitados": resumo.itens_rejeitados,
                "status_financeiro": resumo.status_financeiro,
                "tem_divergencia": resumo.tem_divergencia,
            }
            for resumo in distribuicao["clientes"]
        ],
    }


def montar_payload_financeiro(relatorio):
    return {
        "resumo_global": {
            "despesas_tecnico": _str_money(relatorio.total_despesas_tecnico),
            "despesas_empresa": _str_money(relatorio.total_despesas_empresa),
            "km": _str_money(relatorio.total_km),
            "km_reembolso_tecnico": _str_money(relatorio.total_km_reembolso_tecnico),
            "km_excesso_reducao": _str_money(relatorio.total_km_excesso_reducao_clientes),
            "total_solicitado": _str_money(relatorio.total_solicitado),
            "total_aprovado_despesas": _str_money(relatorio.total_aprovado_despesas),
            "total_aprovado_km": _str_money(relatorio.total_aprovado_km),
            "total_aprovado": _str_money(relatorio.total_aprovado),
            "diferenca_removida": _str_money(relatorio.diferenca_removida),
            "adiantamento": _str_money(relatorio.valor_adiantamento),
            "saldo_aprovado": _str_money(relatorio.saldo_aprovado),
            "total_despesas": relatorio.despesas.count(),
            "total_trechos": relatorio.trechos.count(),
        },
        "despesas": [_serializar_despesa(despesa) for despesa in relatorio.despesas.all()],
        "trechos": [_serializar_trecho(trecho) for trecho in relatorio.trechos.all()],
        "resumo_clientes": _serializar_clientes(relatorio),
        "alertas": validar_integridade_financeira_relatorio(relatorio),
    }


def montar_payload_financeiro_por_id(relatorio_id):
    relatorio = carregar_relatorio_financeiro(relatorio_id)
    return montar_payload_financeiro(relatorio)
