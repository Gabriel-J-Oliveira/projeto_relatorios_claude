import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from relatorios.models import Cliente, TrechoKm, TrechoKMCliente, TrechoRateioKM
from relatorios.services.autorizacao_service import (
    usuario_eh_admin_erp,
    usuario_eh_financeiro,
    usuario_tem_acesso_total,
)
from relatorios.services.km_financeiro_service import (
    filtro_empresas_internas_grupo_q,
    valor_km_cliente_contratual,
)


logger = logging.getLogger("relatorios.clientes.valor_km")


def usuario_pode_configurar_valor_km(user):
    return (
        usuario_tem_acesso_total(user)
        or usuario_eh_financeiro(user)
        or usuario_eh_admin_erp(user)
    )


def clientes_pendentes_valor_km(usuario=None, apenas_api_novos=False):
    if usuario is not None and not usuario_pode_configurar_valor_km(usuario):
        return Cliente.objects.none()
    qs = Cliente.objects.filter(ativo=True).filter(
        Q(valor_km__isnull=True) | Q(valor_km__lte=0)
    ).exclude(
        filtro_empresas_internas_grupo_q()
    ).order_by("nome_fantasia", "razao_social", "nome")
    if apenas_api_novos:
        qs = qs.filter(valor_km_pendente_api_novo=True)
    return qs


def normalizar_valor_km(valor):
    texto = str(valor or "").strip()
    if not texto:
        raise ValidationError("Informe o valor de KM.")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        decimal = Decimal(texto).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Valor de KM invalido.") from exc
    if decimal <= 0:
        raise ValidationError("Valor de KM deve ser maior que zero.")
    return decimal


def salvar_valor_km_cliente(cliente, valor_km, usuario, observacao=""):
    if not usuario_pode_configurar_valor_km(usuario):
        logger.warning(
            "Tentativa sem permissao de alterar valor_km. usuario=%s cliente=%s",
            getattr(usuario, "pk", None),
            cliente.pk,
        )
        raise PermissionDenied("Voce nao tem permissao para alterar valor de KM.")

    valor_novo = normalizar_valor_km(valor_km)
    valor_anterior = cliente.valor_km
    if valor_anterior == valor_novo and (observacao or "") == (cliente.valor_km_observacao or ""):
        return False

    cliente.valor_km = valor_novo
    cliente.valor_km_pendente_api_novo = False
    cliente.valor_km_atualizado_em = timezone.now()
    cliente.valor_km_atualizado_por = usuario
    if observacao is not None:
        cliente.valor_km_observacao = str(observacao or "").strip()
    cliente.save(
        update_fields=[
            "valor_km",
            "valor_km_atualizado_em",
            "valor_km_atualizado_por",
            "valor_km_observacao",
            "valor_km_pendente_api_novo",
        ]
    )
    logger.info(
        "Valor KM de cliente atualizado. usuario=%s cliente=%s anterior=%s novo=%s",
        getattr(usuario, "pk", None),
        cliente.pk,
        valor_anterior,
        valor_novo,
    )
    return True


@transaction.atomic
def salvar_valores_km_clientes(itens, usuario):
    if not usuario_pode_configurar_valor_km(usuario):
        raise PermissionDenied("Voce nao tem permissao para alterar valor de KM.")

    atualizados = 0
    erros = []
    for item in itens:
        cliente_id = item.get("cliente_id")
        try:
            cliente = Cliente.objects.select_for_update().get(pk=cliente_id, ativo=True)
            mudou = salvar_valor_km_cliente(
                cliente,
                item.get("valor_km"),
                usuario,
                item.get("observacao", ""),
            )
            if mudou:
                atualizados += 1
        except Cliente.DoesNotExist:
            erros.append(f"Cliente {cliente_id} nao encontrado ou inativo.")
        except ValidationError as exc:
            erros.append(f"Cliente {cliente_id}: {' '.join(exc.messages)}")
    if erros:
        raise ValidationError(erros)
    return atualizados


def clientes_relatorio_sem_valor_km(relatorio):
    if not getattr(relatorio, "pk", None):
        return []

    trecho_ids = list(
        TrechoKm.objects.filter(relatorio_id=relatorio.pk).values_list("pk", flat=True)
    )
    if not trecho_ids:
        return []

    cliente_ids = set(
        TrechoKMCliente.objects.filter(trecho_id__in=trecho_ids).values_list(
            "cliente_id", flat=True
        )
    )
    cliente_ids.update(
        TrechoRateioKM.objects.filter(trecho_id__in=trecho_ids).values_list(
            "cliente_id", flat=True
        )
    )

    # Compatibilidade com relatórios antigos de cliente único, anteriores ao vínculo
    # explícito entre trecho e cliente.
    if not cliente_ids:
        clientes_relatorio_ids = list(
            relatorio.clientes_vinculados.values_list("cliente_id", flat=True)[:2]
        )
        if len(clientes_relatorio_ids) == 1:
            cliente_ids.add(clientes_relatorio_ids[0])
        elif not clientes_relatorio_ids and relatorio.cliente_id:
            cliente_ids.add(relatorio.cliente_id)

    if not cliente_ids:
        return []

    # Consulta nova a cada validação para refletir valor_km recém-atualizado.
    clientes = Cliente.objects.filter(pk__in=cliente_ids, ativo=True).order_by(
        "nome_fantasia",
        "razao_social",
        "nome",
    )
    return [
        cliente
        for cliente in clientes
        if valor_km_cliente_contratual(cliente) is None
    ]


def erros_clientes_sem_valor_km_relatorio(relatorio):
    pendentes = clientes_relatorio_sem_valor_km(relatorio)
    return [
        f"Nao e possivel aprovar este relatorio: o cliente {cliente.nome_exibicao} nao possui valor padrao de KM cadastrado."
        for cliente in pendentes
    ]
