from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

from relatorios.services.politica_valor_service import valor_km_control_sul


CENTAVO = Decimal("0.01")
DECIMAL_KM = Decimal("0.01")
DECIMAL_VALOR_KM = Decimal("0.0001")
VALOR_KM_REEMBOLSO_TECNICO = Decimal("1.35")


def _decimal(valor, casas):
    return Decimal(str(valor or "0")).quantize(casas, rounding=ROUND_HALF_UP)


def money(valor):
    return _decimal(valor, CENTAVO)


def km_decimal(valor):
    return _decimal(valor, DECIMAL_KM)


def valor_km_decimal(valor):
    return _decimal(valor, DECIMAL_VALOR_KM)


def valor_km_reembolso_tecnico():
    return valor_km_decimal(
        valor_km_control_sul()
        or getattr(settings, "VALOR_KM_CONTROLSUL", VALOR_KM_REEMBOLSO_TECNICO)
    )


def valor_km_cliente_contratual(cliente):
    valor = getattr(cliente, "valor_km", None)
    if valor in (None, ""):
        return None
    valor = valor_km_decimal(valor)
    return valor if valor > 0 else None


def tipo_diferenca(valor):
    valor = money(valor)
    if valor > 0:
        return "EXCESSO"
    if valor < 0:
        return "REDUCAO"
    return "NEUTRO"


def calcular_km_financeiro(km, cliente=None, valor_final_cliente=None):
    km = km_decimal(km)
    valor_reembolso_unitario = valor_km_reembolso_tecnico()
    valor_reembolso_tecnico = money(km * valor_reembolso_unitario)
    valor_km_cliente = valor_km_cliente_contratual(cliente) if cliente is not None else None
    valor_cobranca_calculado = (
        money(km * valor_km_cliente) if valor_km_cliente is not None else None
    )
    valor_cobranca_cliente = (
        money(valor_final_cliente)
        if valor_final_cliente is not None
        else valor_cobranca_calculado
    )
    diferenca = (
        money(valor_cobranca_cliente - valor_reembolso_tecnico)
        if valor_cobranca_cliente is not None
        else None
    )
    return {
        "km": km,
        "valor_km_reembolso_tecnico": valor_reembolso_unitario,
        "valor_reembolso_tecnico": valor_reembolso_tecnico,
        "valor_km_cliente": valor_km_cliente,
        "valor_cobranca_calculado": valor_cobranca_calculado,
        "valor_cobranca_cliente": valor_cobranca_cliente,
        "diferenca": diferenca,
        "tipo_diferenca": tipo_diferenca(diferenca or Decimal("0.00")),
        "cliente_sem_valor_km": valor_km_cliente is None,
    }
