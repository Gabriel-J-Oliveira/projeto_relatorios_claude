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
def reabrir_relatorio_aprovado(relatorio_id, usuario):
    relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
    status_anterior = relatorio.status

    if status_anterior != StatusRelatorio.APROVADO:
        raise ReabrirRelatorioError(
            "Somente relatórios aprovados podem ser reabertos."
        )

    relatorio.status = StatusRelatorio.CONFERENCIA
    relatorio.save(update_fields=["status", "atualizado_em"])
    despesas_recalculadas = _recalcular_politicas_e_rateios(relatorio)

    registrar_evento(
        relatorio,
        usuario,
        TipoEventoHistorico.REABERTO,
        (
            "Relatório reaberto administrativamente por Gabriel Oliveira. "
            'Status alterado de "Aprovado" para "Conferência pendente".'
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
