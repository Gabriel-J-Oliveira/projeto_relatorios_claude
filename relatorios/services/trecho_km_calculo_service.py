from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction

from relatorios.models import Cliente, StatusRateio, TipoEventoHistorico, TrechoRateioKM
from relatorios.services.historico_service import registrar_evento
from relatorios.services.rateio_exceptions import RateioError


CENTAVO = Decimal("0.01")
DECIMAL_KM = Decimal("0.1")
DECIMAL_VALOR_KM = Decimal("0.0001")


def _decimal(valor, casas, mensagem):
    texto = str(valor or "0").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(texto).quantize(casas, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise RateioError(mensagem)
    if numero < 0:
        raise RateioError("Valores de KM não podem ser negativos.")
    return numero


def _money(valor):
    return _decimal(valor, CENTAVO, "Valor de KM inválido.")


def _km(valor):
    return _decimal(valor, DECIMAL_KM, "KM inválido.")


def _valor_km(valor):
    return _decimal(valor, DECIMAL_VALOR_KM, "Valor/KM inválido.")


def _valor_km_cliente(cliente, trecho):
    valor_cliente = getattr(cliente, "valor_km", None)
    return _valor_km(valor_cliente if valor_cliente not in (None, "") else trecho.valor_km)


def _clientes_ids_item(trecho):
    ids = list(dict.fromkeys(trecho.clientes_vinculados.values_list("cliente_id", flat=True)))
    if ids:
        return ids
    ids = list(
        dict.fromkeys(
            trecho.relatorio.clientes_vinculados.values_list("cliente_id", flat=True)
        )
    )
    if len(ids) == 1:
        return ids
    if len(ids) > 1:
        return []
    return [trecho.relatorio.cliente_id] if trecho.relatorio.cliente_id else []


def _status_manual(status):
    return status in {StatusRateio.ADJUSTED, StatusRateio.APPROVED}


class TrechoKMCalculoService:
    @staticmethod
    def sincronizar(trecho):
        cliente_ids = _clientes_ids_item(trecho)
        if not cliente_ids:
            return []

        rejeitado = trecho.rejeitado or trecho.status_financeiro == "rejeitado"
        existentes = {
            calculo.cliente_id: calculo
            for calculo in trecho.rateios.select_related("cliente")
        }
        clientes_por_id = Cliente.objects.in_bulk(cliente_ids)
        trecho.rateios.exclude(cliente_id__in=cliente_ids).delete()
        total_clientes = len(cliente_ids)

        for cliente_id in cliente_ids:
            calculo = existentes.get(cliente_id)
            if rejeitado:
                cliente = calculo.cliente if calculo else clientes_por_id[cliente_id]
                valor_km = _valor_km_cliente(cliente, trecho)
                valor_calculado = _money(trecho.km * valor_km)
                TrechoRateioKM.objects.update_or_create(
                    trecho=trecho,
                    cliente_id=cliente_id,
                    defaults={
                        "km_original": trecho.km,
                        "km_final": trecho.km,
                        "valor_rateado": Decimal("0.00"),
                        "km_cliente": trecho.km,
                        "valor_km": valor_km,
                        "valor_calculado": valor_calculado,
                        "valor_final": Decimal("0.00"),
                        "status": StatusRateio.AUTO,
                    },
                )
                continue
            if calculo and _status_manual(calculo.status):
                updates = []
                if calculo.km_cliente == Decimal("0.0") and calculo.km_final:
                    calculo.km_cliente = calculo.km_final
                    updates.append("km_cliente")
                if calculo.valor_final == Decimal("0.00") and calculo.valor_rateado:
                    calculo.valor_final = calculo.valor_rateado
                    updates.append("valor_final")
                if calculo.valor_calculado == Decimal("0.00") and calculo.valor_rateado:
                    calculo.valor_calculado = calculo.valor_rateado
                    updates.append("valor_calculado")
                if calculo.valor_km == Decimal("0.0000"):
                    calculo.valor_km = _valor_km_cliente(calculo.cliente, trecho)
                    updates.append("valor_km")
                if updates:
                    calculo.save(update_fields=updates + ["updated_at"])
                continue

            cliente = clientes_por_id[cliente_id]
            valor_km = (
                _valor_km(trecho.valor_km_final)
                if total_clientes == 1
                else _valor_km_cliente(cliente, trecho)
            )
            km_cliente = Decimal("0.0") if rejeitado else _km(trecho.km)
            valor_calculado = Decimal("0.00") if rejeitado else _money(km_cliente * valor_km)

            TrechoRateioKM.objects.update_or_create(
                trecho=trecho,
                cliente_id=cliente_id,
                defaults={
                    "km_original": km_cliente,
                    "km_final": km_cliente,
                    "valor_rateado": valor_calculado,
                    "km_cliente": km_cliente,
                    "valor_km": valor_km,
                    "valor_calculado": valor_calculado,
                    "valor_final": valor_calculado,
                    "status": StatusRateio.AUTO,
                },
            )
        return list(trecho.rateios.select_related("cliente"))

    @staticmethod
    def salvar(trecho, dados_calculo, usuario, motivo="", aprovar=False):
        motivo = (motivo or "").strip()
        TrechoKMCalculoService.sincronizar(trecho)
        atuais_pre = {calculo.cliente_id: calculo for calculo in trecho.rateios.all()}
        try:
            payload_clientes = {int(item["cliente_id"]) for item in dados_calculo}
        except (KeyError, TypeError, ValueError):
            raise RateioError("Payload de cálculo de KM inválido.")
        if payload_clientes != set(atuais_pre):
            raise RateioError("O cálculo enviado não corresponde aos clientes do trecho.")

        houve_alteracao = False
        for item in dados_calculo:
            calculo_pre = atuais_pre.get(int(item["cliente_id"]))
            if not calculo_pre:
                continue
            if "valor_km" in item:
                houve_alteracao = (
                    houve_alteracao
                    or calculo_pre.valor_km != _valor_km(item.get("valor_km"))
                )
            else:
                houve_alteracao = (
                    houve_alteracao
                    or calculo_pre.valor_final
                    != _money(item.get("valor_final", item.get("valor_rateado")))
                )
        if houve_alteracao and not motivo:
            raise RateioError("Informe o motivo da alteracao manual do KM.")

        with transaction.atomic():
            atuais = {
                calculo.cliente_id: calculo
                for calculo in trecho.rateios.select_for_update().select_related("cliente")
            }
            alteracoes = []
            for item in dados_calculo:
                cliente_id = int(item["cliente_id"])
                if cliente_id not in atuais:
                    raise RateioError("Cliente invalido para o calculo deste trecho.")
                calculo = atuais[cliente_id]
                valor_km_anterior = calculo.valor_km
                valor_anterior = calculo.valor_final
                if "valor_km" in item:
                    valor_km_novo = _valor_km(item.get("valor_km"))
                    valor_novo = _money(calculo.km_cliente * valor_km_novo)
                else:
                    valor_km_novo = calculo.valor_km
                    valor_novo = _money(item.get("valor_final", item.get("valor_rateado")))
                status = StatusRateio.APPROVED if aprovar else (
                    StatusRateio.ADJUSTED
                    if valor_anterior != valor_novo or valor_km_anterior != valor_km_novo
                    else calculo.status
                )
                calculo.valor_km = valor_km_novo
                calculo.valor_calculado = valor_novo
                calculo.valor_final = valor_novo
                calculo.valor_rateado = valor_novo
                calculo.status = status
                if valor_anterior != valor_novo or valor_km_anterior != valor_km_novo or aprovar:
                    calculo.alterado_por = usuario
                    if motivo:
                        calculo.motivo_ajuste = motivo
                calculo.save()
                if valor_anterior != valor_novo or valor_km_anterior != valor_km_novo:
                    alteracoes.append(
                        {
                            "cliente_id": cliente_id,
                            "valor_km_anterior": str(valor_km_anterior),
                            "valor_km_novo": str(valor_km_novo),
                            "valor_anterior": str(valor_anterior),
                            "valor_novo": str(valor_novo),
                        }
                    )

            if alteracoes or aprovar:
                registrar_evento(
                    trecho.relatorio,
                    usuario,
                    TipoEventoHistorico.VALOR_ALTERADO,
                    "Cálculo de KM alterado pelo financeiro."
                    if alteracoes
                    else "Cálculo de KM aprovado.",
                    {
                        "tipo_item": "trecho",
                        "item_id": trecho.pk,
                        "motivo": motivo,
                        "alteracoes": alteracoes,
                        "aprovado": aprovar,
                    },
                )
        return list(trecho.rateios.select_related("cliente"))

    @staticmethod
    def validar_trecho(trecho):
        erros = []
        try:
            TrechoKMCalculoService.sincronizar(trecho)
        except RateioError as exc:
            return [f"Trecho {trecho.pk}: {exc}"]

        for calculo in trecho.rateios.select_related("cliente"):
            if calculo.valor_final < 0 or calculo.valor_calculado < 0:
                erros.append(f"Trecho {trecho.pk}: cálculo de KM negativo.")
            esperado = _money(calculo.km_cliente * calculo.valor_km)
            if calculo.valor_calculado != esperado:
                erros.append(
                    f"Trecho {trecho.pk}: cálculo automático inconsistente para {calculo.cliente.nome}."
                )
        if trecho.valor_final > 0 and not trecho.rateios.exists():
            erros.append(f"Trecho {trecho.pk} não possui cálculo por cliente.")
        return erros


def sincronizar_calculo_trecho(trecho):
    return TrechoKMCalculoService.sincronizar(trecho)


def salvar_calculo_trecho(trecho, dados_calculo, usuario, motivo="", aprovar=False):
    return TrechoKMCalculoService.salvar(
        trecho, dados_calculo, usuario, motivo=motivo, aprovar=aprovar
    )


def validar_calculos_trecho(trecho):
    return TrechoKMCalculoService.validar_trecho(trecho)


def serializar_calculo_trecho(calculo):
    return {
        "cliente_id": calculo.cliente_id,
        "cliente": calculo.cliente.nome,
        "km_cliente": str(calculo.km_cliente),
        "valor_km": str(calculo.valor_km),
        "valor_calculado": str(calculo.valor_calculado),
        "valor_final": str(calculo.valor_final),
        "status": calculo.status,
        "status_label": calculo.get_status_display(),
    }
