import logging

from django.db import transaction

from relatorios.models import RelatorioTecnico, StatusRelatorio, TipoEventoHistorico
from relatorios.services.historico_service import registrar_evento
from relatorios.services.politica_aprovacao_service import (
    aplicar_politica_valor_aprovado_inicial,
)
from relatorios.services.rateio_service import RateioError, garantir_rateios_relatorio


logger = logging.getLogger(__name__)


class ReabrirRelatorioError(Exception):
    pass


STATUS_REABRIVEIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


def _status_label(status):
    return dict(StatusRelatorio.choices).get(status, status)


def _recalcular_politicas_e_rateios(relatorio):
    despesas_recalculadas = []
    for despesa in relatorio.despesas.select_for_update().prefetch_related(
        "tecnicos_vinculados"
    ):
        valor_anterior = despesa.valor_aprovado
        aplicar_politica_valor_aprovado_inicial(despesa, preservar_manual=False)
        despesa.refresh_from_db(fields=["valor_aprovado"])
        if despesa.valor_aprovado != valor_anterior:
            despesas_recalculadas.append(
                {
                    "despesa_id": despesa.pk,
                    "valor_aprovado_anterior": str(valor_anterior or ""),
                    "valor_aprovado_novo": str(despesa.valor_aprovado or ""),
                }
            )
    try:
        garantir_rateios_relatorio(relatorio)
    except RateioError as exc:
        raise ReabrirRelatorioError(str(exc)) from exc
    return despesas_recalculadas


@transaction.atomic
def reabrir_relatorio_finalizado(relatorio_id, usuario):
    relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
    status_anterior = relatorio.status

    if status_anterior not in STATUS_REABRIVEIS:
        raise ReabrirRelatorioError(
            "Somente relatórios aprovados ou rejeitados podem ser reabertos."
        )

    relatorio.status = StatusRelatorio.CONFERENCIA
    relatorio.save(update_fields=["status", "atualizado_em"])
    despesas_recalculadas = _recalcular_politicas_e_rateios(relatorio)

    registrar_evento(
        relatorio,
        usuario,
        TipoEventoHistorico.REABERTO,
        (
            "Relatório reaberto administrativamente. "
            f'Status alterado de "{_status_label(status_anterior)}" '
            f'para "{_status_label(StatusRelatorio.CONFERENCIA)}".'
        ),
        {
            "status_anterior": status_anterior,
            "status_novo": StatusRelatorio.CONFERENCIA,
            "aprovado_em_preservado": (
                relatorio.aprovado_em.isoformat() if relatorio.aprovado_em else None
            ),
            "aprovado_por_id_preservado": relatorio.aprovado_por_id,
            "politicas_recalculadas": True,
            "despesas_recalculadas": despesas_recalculadas,
        },
    )
    logger.info(
        "RELATORIO_REABERTO_ADMIN relatorio=%s usuario=%s status_anterior=%s status_novo=%s despesas_recalculadas=%s",
        relatorio.pk,
        getattr(usuario, "pk", None),
        status_anterior,
        relatorio.status,
        len(despesas_recalculadas),
    )
    return relatorio


def reabrir_relatorio_aprovado(relatorio_id, usuario):
    return reabrir_relatorio_finalizado(relatorio_id, usuario)
