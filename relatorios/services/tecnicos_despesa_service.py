import logging

from django.db import transaction

from relatorios.models import (
    DespesaTecnico,
    StatusRelatorio,
    Tecnico,
)


logger = logging.getLogger(__name__)

ESTADOS_FINAIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


def normalizar_ids_tecnicos(valor):
    if valor is None:
        return []
    if isinstance(valor, str):
        partes = valor.replace(";", ",").split(",")
    else:
        partes = valor

    ids = []
    vistos = set()
    for parte in partes:
        try:
            tecnico_id = int(str(parte).strip())
        except (TypeError, ValueError):
            continue
        if tecnico_id <= 0 or tecnico_id in vistos:
            continue
        vistos.add(tecnico_id)
        ids.append(tecnico_id)
    return ids


def tecnicos_relatorio_ids(relatorio):
    if not relatorio:
        return set()
    ids = set(relatorio.tecnicos_envolvidos_ids())
    return {int(tecnico_id) for tecnico_id in ids if tecnico_id}


def validar_tecnicos_despesa_no_relatorio(relatorio, tecnico_ids):
    tecnico_ids = normalizar_ids_tecnicos(tecnico_ids)
    permitidos = tecnicos_relatorio_ids(relatorio)
    invalidos = sorted(set(tecnico_ids) - permitidos)
    if invalidos:
        return ["Selecione apenas técnicos vinculados ao relatório para esta despesa."]
    return []


@transaction.atomic
def sync_tecnicos_despesa(despesa, tecnico_ids, usuario=None):
    if despesa.relatorio.status in ESTADOS_FINAIS:
        raise ValueError("Relatório finalizado não pode ter técnicos da despesa alterados.")

    tecnico_ids = normalizar_ids_tecnicos(tecnico_ids)
    erros = validar_tecnicos_despesa_no_relatorio(despesa.relatorio, tecnico_ids)
    if erros:
        return erros

    anteriores = set(
        DespesaTecnico.objects.filter(despesa=despesa).values_list("tecnico_id", flat=True)
    )
    novos = set(tecnico_ids)

    removidos = anteriores - novos
    adicionados = novos - anteriores

    DespesaTecnico.objects.filter(despesa=despesa).exclude(
        tecnico_id__in=tecnico_ids
    ).delete()
    for tecnico_id in tecnico_ids:
        DespesaTecnico.objects.get_or_create(despesa=despesa, tecnico_id=tecnico_id)

    for tecnico_id in adicionados:
        logger.info(
            "TECNICO_ADICIONADO relatorio=%s despesa=%s tecnico=%s usuario=%s",
            despesa.relatorio_id,
            despesa.pk,
            tecnico_id,
            getattr(usuario, "pk", None),
        )
    for tecnico_id in removidos:
        logger.info(
            "TECNICO_REMOVIDO relatorio=%s despesa=%s tecnico=%s usuario=%s",
            despesa.relatorio_id,
            despesa.pk,
            tecnico_id,
            getattr(usuario, "pk", None),
        )
    return []


@transaction.atomic
def remover_tecnicos_despesas_fora_relatorio(relatorio, usuario=None):
    permitidos = tecnicos_relatorio_ids(relatorio)
    qs = DespesaTecnico.objects.select_related("despesa").filter(
        despesa__relatorio=relatorio,
    )
    if permitidos:
        qs = qs.exclude(tecnico_id__in=permitidos)
    removidos = list(qs.values_list("despesa_id", "tecnico_id"))
    qs.delete()
    for despesa_id, tecnico_id in removidos:
        logger.info(
            "TECNICO_REMOVIDO relatorio=%s despesa=%s tecnico=%s usuario=%s",
            relatorio.pk,
            despesa_id,
            tecnico_id,
            getattr(usuario, "pk", None),
        )
    return removidos


def tecnicos_despesa(despesa):
    return Tecnico.objects.filter(
        despesas_participantes__despesa=despesa,
    ).order_by("nome")
