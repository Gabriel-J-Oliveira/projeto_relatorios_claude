from django.db import transaction
from django.db.models import Q

from relatorios.models import (
    Cliente,
    DespesaCliente,
    EmpresaGrupo,
    RelatorioCliente,
    StatusRelatorio,
    TrechoKMCliente,
    normalizar_texto_busca,
)


ESTADOS_FINAIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}
EMPRESAS_GRUPO_TERMOS = {
    EmpresaGrupo.BLAZIUS_E_LORENZETTI: "BLAZIUS E LORENZETTI",
    EmpresaGrupo.CONTROLSUL: "CONTROLSUL",
    EmpresaGrupo.FISCALMAX: "FISCALMAX",
}


def _bloquear_finalizado(relatorio):
    if relatorio.status in ESTADOS_FINAIS:
        raise ValueError("Relatorio finalizado nao pode ter clientes alterados.")


def normalizar_ids_clientes(valor):
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
            cliente_id = int(str(parte).strip())
        except (TypeError, ValueError):
            continue
        if cliente_id <= 0 or cliente_id in vistos:
            continue
        vistos.add(cliente_id)
        ids.append(cliente_id)
    return ids


def resolver_cliente_empresa_grupo(empresa_grupo):
    termo = EMPRESAS_GRUPO_TERMOS.get(empresa_grupo)
    if not termo:
        return None

    candidatos = list(
        Cliente.objects.filter(ativo=True)
        .filter(
            Q(nome__icontains=termo)
            | Q(razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
        )
        .distinct()
        .order_by("pk")
    )
    termo_normalizado = normalizar_texto_busca(termo)
    exatos = [
        cliente
        for cliente in candidatos
        if termo_normalizado
        in {
            normalizar_texto_busca(cliente.nome),
            normalizar_texto_busca(cliente.razao_social),
            normalizar_texto_busca(cliente.nome_fantasia),
        }
    ]
    if len(exatos) == 1:
        return exatos[0]
    if len(candidatos) == 1:
        return candidatos[0]
    return None


def obter_clientes_relatorio(relatorio):
    return relatorio.clientes_relacionados()


def obter_motivos_clientes_relatorio(relatorio):
    if not relatorio or not getattr(relatorio, "pk", None):
        return {}
    return {
        vinculo.cliente_id: vinculo.motivo_viagem or ""
        for vinculo in relatorio.clientes_vinculados.all()
    }


def clientes_despesa(despesa):
    qs = Cliente.objects.filter(despesas_cliente__despesa=despesa).order_by("nome")
    if qs.exists():
        return qs
    return obter_clientes_relatorio(despesa.relatorio)


def clientes_trecho(trecho):
    qs = Cliente.objects.filter(trechos_km_cliente__trecho=trecho).order_by("nome")
    if qs.exists():
        return qs
    return obter_clientes_relatorio(trecho.relatorio)


@transaction.atomic
def sync_clientes_relatorio(relatorio, cliente_ids, motivos_por_cliente=None):
    _bloquear_finalizado(relatorio)
    cliente_ids = normalizar_ids_clientes(cliente_ids)
    motivos_por_cliente = motivos_por_cliente or {}
    clientes_validos = list(
        Cliente.objects.filter(pk__in=cliente_ids, ativo=True).order_by("nome")
    )
    ids_validos = [cliente.pk for cliente in clientes_validos]

    RelatorioCliente.objects.filter(relatorio=relatorio).exclude(
        cliente_id__in=ids_validos
    ).delete()

    for ordem, cliente_id in enumerate(ids_validos):
        motivo = str(
            motivos_por_cliente.get(cliente_id)
            or motivos_por_cliente.get(str(cliente_id))
            or ""
        ).strip()
        defaults = {"ordem": ordem, "motivo_viagem": motivo}
        RelatorioCliente.objects.update_or_create(
            relatorio=relatorio,
            cliente_id=cliente_id,
            defaults=defaults,
        )

    if ids_validos and relatorio.cliente_id != ids_validos[0]:
        relatorio.cliente_id = ids_validos[0]
        relatorio.save(update_fields=["cliente", "atualizado_em"])

    DespesaCliente.objects.filter(despesa__relatorio=relatorio).exclude(
        cliente_id__in=ids_validos
    ).delete()
    TrechoKMCliente.objects.filter(trecho__relatorio=relatorio).exclude(
        cliente_id__in=ids_validos
    ).delete()

    from relatorios.services.rateio_service import (
        garantir_rateio_despesa,
        garantir_rateio_trecho,
    )

    for despesa in relatorio.despesas.all():
        garantir_rateio_despesa(despesa)
    for trecho in relatorio.trechos.all():
        garantir_rateio_trecho(trecho)

    return ids_validos


def validar_clientes_item_no_relatorio(relatorio, cliente_ids):
    cliente_ids = normalizar_ids_clientes(cliente_ids)
    clientes_relatorio = set(
        obter_clientes_relatorio(relatorio).values_list("pk", flat=True)
    )
    if not cliente_ids:
        return ["Selecione ao menos um cliente envolvido."]
    invalidos = sorted(set(cliente_ids) - clientes_relatorio)
    if invalidos:
        return ["Item referencia cliente fora deste relatorio."]
    return []


def _cliente_ids_item(cliente_ids, relatorio):
    cliente_ids = normalizar_ids_clientes(cliente_ids)
    clientes_relatorio = list(
        obter_clientes_relatorio(relatorio).values_list("pk", flat=True)
    )
    if not cliente_ids and len(clientes_relatorio) == 1:
        return clientes_relatorio
    return cliente_ids


@transaction.atomic
def sync_clientes_despesa(despesa, cliente_ids):
    _bloquear_finalizado(despesa.relatorio)
    cliente_ids = _cliente_ids_item(cliente_ids, despesa.relatorio)
    erros = validar_clientes_item_no_relatorio(despesa.relatorio, cliente_ids)
    if erros:
        return erros
    DespesaCliente.objects.filter(despesa=despesa).exclude(
        cliente_id__in=cliente_ids
    ).delete()
    for cliente_id in cliente_ids:
        DespesaCliente.objects.get_or_create(despesa=despesa, cliente_id=cliente_id)
    from relatorios.services.rateio_service import garantir_rateio_despesa

    garantir_rateio_despesa(despesa)
    return []


@transaction.atomic
def sync_clientes_trecho(trecho, cliente_ids):
    _bloquear_finalizado(trecho.relatorio)
    cliente_ids = _cliente_ids_item(cliente_ids, trecho.relatorio)
    erros = validar_clientes_item_no_relatorio(trecho.relatorio, cliente_ids)
    if erros:
        return erros
    TrechoKMCliente.objects.filter(trecho=trecho).exclude(
        cliente_id__in=cliente_ids
    ).delete()
    for cliente_id in cliente_ids:
        TrechoKMCliente.objects.get_or_create(trecho=trecho, cliente_id=cliente_id)
    from relatorios.services.rateio_service import garantir_rateio_trecho

    garantir_rateio_trecho(trecho)
    return []
