import logging

from django.db import transaction

from relatorios.models import RelatorioTecnico, StatusRelatorio, TipoEventoHistorico
from relatorios.services.historico_service import registrar_evento


logger = logging.getLogger(__name__)


class ReabrirRelatorioError(Exception):
    pass


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
        },
    )
    logger.info(
        "RELATORIO_REABERTO_ADMIN relatorio=%s usuario=%s status_anterior=%s status_novo=%s",
        relatorio.pk,
        getattr(usuario, "pk", None),
        status_anterior,
        relatorio.status,
    )
    return relatorio
