from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from relatorios.models import (
    DespesaRateio,
    StatusRateio,
    TipoEventoHistorico,
)
from relatorios.services.historico_service import registrar_evento
from relatorios.services.rateio_exceptions import RateioError
from relatorios.services.trecho_km_calculo_service import (
    salvar_calculo_trecho,
    serializar_calculo_trecho,
    sincronizar_calculo_trecho,
    validar_calculos_trecho,
)


CENTAVO = Decimal("0.01")


def _money(valor):
    texto = str(valor or "0").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(texto).quantize(
            CENTAVO, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        raise RateioError("Valor de rateio inválido.")
    if numero < 0:
        raise RateioError("Valor de rateio não pode ser negativo.")
    return numero



def _dividir_decimal(total, quantidade, casas=CENTAVO):
    total = Decimal(total or "0").quantize(casas, rounding=ROUND_HALF_UP)
    if quantidade <= 0:
        return []
    base = (total / Decimal(quantidade)).quantize(casas, rounding=ROUND_HALF_UP)
    partes = [base for _ in range(quantidade)]
    partes[-1] = (total - sum(partes[:-1], Decimal("0"))).quantize(
        casas, rounding=ROUND_HALF_UP
    )
    return partes


def _clientes_ids_item(item):
    ids = list(dict.fromkeys(item.clientes_vinculados.values_list("cliente_id", flat=True)))
    if ids:
        return ids
    relatorio = item.relatorio
    ids = list(dict.fromkeys(relatorio.clientes_vinculados.values_list("cliente_id", flat=True)))
    if ids:
        return ids
    return [relatorio.cliente_id] if relatorio.cliente_id else []


def _percentual(valor, total):
    total = Decimal(total or "0")
    if total == 0:
        return None
    return ((Decimal(valor) / total) * Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _status_manual(status):
    return status in {StatusRateio.ADJUSTED, StatusRateio.APPROVED}


def _distribuir_respeitando_manual(total, cliente_ids, existentes, campo_valor):
    manuais = {
        cliente_id: rateio
        for cliente_id, rateio in existentes.items()
        if cliente_id in cliente_ids and _status_manual(rateio.status)
    }
    soma_manual = sum(
        (getattr(rateio, campo_valor) for rateio in manuais.values()), Decimal("0.00")
    ).quantize(CENTAVO)
    restante = (total - soma_manual).quantize(CENTAVO)
    automaticos = [cliente_id for cliente_id in cliente_ids if cliente_id not in manuais]

    if restante < 0:
        raise RateioError("Rateios manuais excedem o valor total do item.")
    if restante > 0 and not automaticos:
        raise RateioError("Rateio manual não fecha com o valor total do item.")

    valores_auto = _dividir_decimal(restante, len(automaticos), CENTAVO)
    return manuais, dict(zip(automaticos, valores_auto))



def garantir_rateio_despesa(despesa):
    cliente_ids = _clientes_ids_item(despesa)
    if not cliente_ids:
        return []

    rejeitado = getattr(despesa, "rejeitado", False) or getattr(despesa, "status_financeiro", "") == "rejeitado"
    total = Decimal("0.00") if rejeitado else _money(despesa.valor_final)
    existentes = {
        rateio.cliente_id: rateio
        for rateio in despesa.rateios.select_related("cliente")
    }
    despesa.rateios.exclude(cliente_id__in=cliente_ids).delete()
    if rejeitado:
        for cliente_id in cliente_ids:
            DespesaRateio.objects.update_or_create(
                despesa=despesa,
                cliente_id=cliente_id,
                defaults={
                    "valor_original": Decimal("0.00"),
                    "valor_final": Decimal("0.00"),
                    "percentual": None,
                    "status": StatusRateio.AUTO,
                },
            )
        return list(despesa.rateios.select_related("cliente"))
    _manuais, valores_por_cliente = _distribuir_respeitando_manual(
        total, cliente_ids, existentes, "valor_final"
    )

    for cliente_id in cliente_ids:
        valor = valores_por_cliente.get(cliente_id)
        rateio = existentes.get(cliente_id)
        if rateio and _status_manual(rateio.status):
            continue
        if valor is None:
            valor = Decimal("0.00")
        DespesaRateio.objects.update_or_create(
            despesa=despesa,
            cliente_id=cliente_id,
            defaults={
                "valor_original": valor,
                "valor_final": valor,
                "percentual": _percentual(valor, total),
                "status": StatusRateio.AUTO,
            },
        )
    return list(despesa.rateios.select_related("cliente"))


def garantir_rateio_trecho(trecho):
    return sincronizar_calculo_trecho(trecho)


def garantir_rateios_relatorio(relatorio):
    for despesa in relatorio.despesas.all():
        garantir_rateio_despesa(despesa)
    for trecho in relatorio.trechos.all():
        garantir_rateio_trecho(trecho)


def _validar_soma(total, valores, mensagem):
    soma = sum((_money(valor) for valor in valores), Decimal("0.00")).quantize(CENTAVO)
    total = _money(total)
    if soma != total:
        diferenca = (total - soma).quantize(CENTAVO)
        raise RateioError(f"{mensagem} Diferença: R$ {diferenca}.")
    return soma


def salvar_rateio_despesa(despesa, dados_rateio, usuario, motivo="", aprovar=False):
    total = _money(despesa.valor_final)
    motivo = (motivo or "").strip()
    valores = [_money(item.get("valor_final")) for item in dados_rateio]
    _validar_soma(total, valores, "O rateio da despesa precisa fechar exatamente.")

    atuais_pre = {rateio.cliente_id: rateio for rateio in despesa.rateios.all()}
    try:
        payload_clientes = {int(item["cliente_id"]) for item in dados_rateio}
    except (KeyError, TypeError, ValueError):
        raise RateioError("Payload de rateio inválido.")
    if payload_clientes != set(atuais_pre):
        raise RateioError("O rateio enviado não corresponde aos clientes da despesa.")
    houve_alteracao = any(
        atuais_pre.get(int(item["cliente_id"]))
        and atuais_pre[int(item["cliente_id"])].valor_final != _money(item.get("valor_final"))
        for item in dados_rateio
    )
    if houve_alteracao and not motivo:
        raise RateioError("Informe o motivo da alteração manual do rateio.")

    with transaction.atomic():
        atuais = {rateio.cliente_id: rateio for rateio in despesa.rateios.select_for_update()}
        alteracoes = []
        for item in dados_rateio:
            cliente_id = int(item["cliente_id"])
            valor_novo = _money(item.get("valor_final"))
            if cliente_id not in atuais:
                raise RateioError("Cliente inválido para o rateio desta despesa.")
            rateio = atuais[cliente_id]
            valor_anterior = rateio.valor_final
            status = StatusRateio.APPROVED if aprovar else (
                StatusRateio.ADJUSTED if valor_anterior != valor_novo else rateio.status
            )
            rateio.valor_final = valor_novo
            rateio.percentual = _percentual(valor_novo, total)
            rateio.status = status
            if valor_anterior != valor_novo or aprovar:
                rateio.alterado_por = usuario
                if motivo:
                    rateio.motivo_ajuste = motivo
            rateio.save()
            if valor_anterior != valor_novo:
                alteracoes.append(
                    {
                        "cliente_id": cliente_id,
                        "valor_anterior": str(valor_anterior),
                        "valor_novo": str(valor_novo),
                    }
                )

        if alteracoes or aprovar:
            registrar_evento(
                despesa.relatorio,
                usuario,
                TipoEventoHistorico.VALOR_ALTERADO,
                "Rateio da despesa alterado pelo financeiro."
                if alteracoes
                else "Rateio da despesa aprovado.",
                {
                    "tipo_item": "despesa",
                    "item_id": despesa.pk,
                    "motivo": motivo,
                    "alteracoes": alteracoes,
                    "aprovado": aprovar,
                },
            )
    return list(despesa.rateios.select_related("cliente"))


def salvar_rateio_trecho(trecho, dados_rateio, usuario, motivo="", aprovar=False):
    return salvar_calculo_trecho(
        trecho, dados_rateio, usuario, motivo=motivo, aprovar=aprovar
    )


def serializar_rateio(rateio):
    if isinstance(rateio, DespesaRateio):
        return {
            "cliente_id": rateio.cliente_id,
            "cliente": rateio.cliente.nome,
            "valor_original": str(rateio.valor_original),
            "valor_final": str(rateio.valor_final),
            "status": rateio.status,
            "status_label": rateio.get_status_display(),
        }
    return serializar_calculo_trecho(rateio)


def validar_rateios_relatorio(relatorio):
    erros = []
    for despesa in relatorio.despesas.all():
        try:
            garantir_rateio_despesa(despesa)
        except RateioError as exc:
            erros.append(f"Despesa {despesa.pk}: {exc}")
            continue
        soma = sum((r.valor_final for r in despesa.rateios.all()), Decimal("0.00")).quantize(CENTAVO)
        total = _money(despesa.valor_final)
        despesa_rejeitada = despesa.rejeitado or despesa.status_financeiro == "rejeitado"
        if despesa_rejeitada and soma != Decimal("0.00"):
            erros.append(f"Despesa {despesa.pk} rejeitada possui rateio maior que zero.")
        if total > 0 and not despesa.rateios.exists():
            erros.append(f"Despesa {despesa.pk} não possui rateio.")
        if soma != total:
            erros.append(
                f"Rateio da despesa {despesa.pk} não fecha: soma R$ {soma}, total R$ {total}."
            )
    for trecho in relatorio.trechos.all():
        erros.extend(validar_calculos_trecho(trecho))
    return erros
