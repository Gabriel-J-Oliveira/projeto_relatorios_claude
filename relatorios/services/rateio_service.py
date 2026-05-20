from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from relatorios.models import (
    DespesaRateio,
    StatusRateio,
    TipoEventoHistorico,
    TrechoRateioKM,
)
from relatorios.services.historico_service import registrar_evento


CENTAVO = Decimal("0.01")
DECIMAL_KM = Decimal("0.1")


class RateioError(Exception):
    pass


def _money(valor):
    try:
        numero = Decimal(str(valor or "0").replace(",", ".")).quantize(
            CENTAVO, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        raise RateioError("Valor de rateio inválido.")
    if numero < 0:
        raise RateioError("Valor de rateio não pode ser negativo.")
    return numero


def _km(valor):
    try:
        numero = Decimal(str(valor or "0").replace(",", ".")).quantize(
            DECIMAL_KM, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        raise RateioError("KM de rateio inválido.")
    if numero < 0:
        raise RateioError("KM de rateio não pode ser negativo.")
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
    ids = list(item.clientes_vinculados.values_list("cliente_id", flat=True))
    if ids:
        return ids
    relatorio = item.relatorio
    ids = list(relatorio.clientes_vinculados.values_list("cliente_id", flat=True))
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


def garantir_rateio_despesa(despesa):
    cliente_ids = _clientes_ids_item(despesa)
    if not cliente_ids:
        return []

    total = _money(despesa.valor_final)
    valores = _dividir_decimal(total, len(cliente_ids), CENTAVO)
    existentes = {
        rateio.cliente_id: rateio
        for rateio in despesa.rateios.select_related("cliente")
    }
    despesa.rateios.exclude(cliente_id__in=cliente_ids).delete()

    for cliente_id, valor in zip(cliente_ids, valores):
        rateio = existentes.get(cliente_id)
        if rateio and rateio.status != StatusRateio.AUTO:
            continue
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
    cliente_ids = _clientes_ids_item(trecho)
    if not cliente_ids:
        return []

    total_valor = _money(trecho.valor_final)
    total_km = _km(trecho.km)
    valores = _dividir_decimal(total_valor, len(cliente_ids), CENTAVO)
    kms = _dividir_decimal(total_km, len(cliente_ids), DECIMAL_KM)
    existentes = {
        rateio.cliente_id: rateio
        for rateio in trecho.rateios.select_related("cliente")
    }
    trecho.rateios.exclude(cliente_id__in=cliente_ids).delete()

    for cliente_id, valor, km in zip(cliente_ids, valores, kms):
        rateio = existentes.get(cliente_id)
        if rateio and rateio.status != StatusRateio.AUTO:
            continue
        TrechoRateioKM.objects.update_or_create(
            trecho=trecho,
            cliente_id=cliente_id,
            defaults={
                "km_original": km,
                "km_final": km,
                "valor_rateado": valor,
                "status": StatusRateio.AUTO,
            },
        )
    return list(trecho.rateios.select_related("cliente"))


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
    payload_clientes = {int(item["cliente_id"]) for item in dados_rateio}
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
                alteracoes.append({
                    "cliente_id": cliente_id,
                    "valor_anterior": str(valor_anterior),
                    "valor_novo": str(valor_novo),
                })

        if alteracoes or aprovar:
            registrar_evento(
                despesa.relatorio,
                usuario,
                TipoEventoHistorico.VALOR_ALTERADO,
                "Rateio da despesa alterado pelo financeiro." if alteracoes else "Rateio da despesa aprovado.",
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
    total = _money(trecho.valor_final)
    motivo = (motivo or "").strip()
    valores = [_money(item.get("valor_rateado")) for item in dados_rateio]
    _validar_soma(total, valores, "O rateio do trecho precisa fechar exatamente.")
    kms = [_km(item.get("km_final")) for item in dados_rateio]
    soma_km = sum(kms, Decimal("0.0")).quantize(DECIMAL_KM)
    if soma_km != _km(trecho.km):
        raise RateioError("A soma dos KM rateados precisa fechar exatamente o KM total do trecho.")

    atuais_pre = {rateio.cliente_id: rateio for rateio in trecho.rateios.all()}
    payload_clientes = {int(item["cliente_id"]) for item in dados_rateio}
    if payload_clientes != set(atuais_pre):
        raise RateioError("O rateio enviado não corresponde aos clientes do trecho.")
    houve_alteracao = any(
        atuais_pre.get(int(item["cliente_id"]))
        and atuais_pre[int(item["cliente_id"])].valor_rateado != _money(item.get("valor_rateado"))
        for item in dados_rateio
    )
    if houve_alteracao and not motivo:
        raise RateioError("Informe o motivo da alteração manual do rateio.")

    with transaction.atomic():
        atuais = {rateio.cliente_id: rateio for rateio in trecho.rateios.select_for_update()}
        alteracoes = []
        for item in dados_rateio:
            cliente_id = int(item["cliente_id"])
            valor_novo = _money(item.get("valor_rateado"))
            km_novo = _km(item.get("km_final"))
            if cliente_id not in atuais:
                raise RateioError("Cliente inválido para o rateio deste trecho.")
            rateio = atuais[cliente_id]
            valor_anterior = rateio.valor_rateado
            status = StatusRateio.APPROVED if aprovar else (
                StatusRateio.ADJUSTED if valor_anterior != valor_novo or rateio.km_final != km_novo else rateio.status
            )
            rateio.valor_rateado = valor_novo
            rateio.km_final = km_novo
            rateio.status = status
            if valor_anterior != valor_novo or aprovar:
                rateio.alterado_por = usuario
                if motivo:
                    rateio.motivo_ajuste = motivo
            rateio.save()
            if valor_anterior != valor_novo:
                alteracoes.append({
                    "cliente_id": cliente_id,
                    "valor_anterior": str(valor_anterior),
                    "valor_novo": str(valor_novo),
                })

        if alteracoes or aprovar:
            registrar_evento(
                trecho.relatorio,
                usuario,
                TipoEventoHistorico.VALOR_ALTERADO,
                "Rateio do trecho KM alterado pelo financeiro." if alteracoes else "Rateio do trecho KM aprovado.",
                {
                    "tipo_item": "trecho",
                    "item_id": trecho.pk,
                    "motivo": motivo,
                    "alteracoes": alteracoes,
                    "aprovado": aprovar,
                },
            )
    return list(trecho.rateios.select_related("cliente"))


def serializar_rateio(rateio):
    cliente_nome = rateio.cliente.nome
    if isinstance(rateio, DespesaRateio):
        return {
            "cliente_id": rateio.cliente_id,
            "cliente": cliente_nome,
            "valor_original": str(rateio.valor_original),
            "valor_final": str(rateio.valor_final),
            "status": rateio.status,
            "status_label": rateio.get_status_display(),
        }
    return {
        "cliente_id": rateio.cliente_id,
        "cliente": cliente_nome,
        "km_original": str(rateio.km_original),
        "km_final": str(rateio.km_final),
        "valor_rateado": str(rateio.valor_rateado),
        "status": rateio.status,
        "status_label": rateio.get_status_display(),
    }


def validar_rateios_relatorio(relatorio):
    erros = []
    for despesa in relatorio.despesas.all():
        garantir_rateio_despesa(despesa)
        soma = sum((r.valor_final for r in despesa.rateios.all()), Decimal("0.00")).quantize(CENTAVO)
        total = _money(despesa.valor_final)
        if soma != total:
            erros.append(
                f"Rateio da despesa {despesa.pk} não fecha: soma R$ {soma}, total R$ {total}."
            )
    for trecho in relatorio.trechos.all():
        garantir_rateio_trecho(trecho)
        soma = sum((r.valor_rateado for r in trecho.rateios.all()), Decimal("0.00")).quantize(CENTAVO)
        total = _money(trecho.valor_final)
        if soma != total:
            erros.append(
                f"Rateio do trecho {trecho.pk} não fecha: soma R$ {soma}, total R$ {total}."
            )
    return erros
