from decimal import Decimal, ROUND_HALF_UP
import unicodedata

from django.conf import settings
from django.db.models import Q

from relatorios.services.politica_valor_service import valor_km_control_sul


CENTAVO = Decimal("0.01")
DECIMAL_KM = Decimal("0.01")
DECIMAL_VALOR_KM = Decimal("0.0001")
VALOR_KM_REEMBOLSO_TECNICO = Decimal("1.35")
VALOR_KM_EMPRESA_GRUPO = Decimal("1.85")
EMPRESAS_INTERNAS_GRUPO_TERMOS = (
    "BLAZIUS E LORENZETTI",
    "CONTROLSUL",
    "FISCALMAX",
    "FISCAL MAX",
)


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


def _normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.upper().split())


def cliente_e_empresa_interna_grupo(cliente):
    if cliente is None:
        return False
    campos = (
        getattr(cliente, "nome", ""),
        getattr(cliente, "razao_social", ""),
        getattr(cliente, "nome_fantasia", ""),
    )
    textos = [_normalizar_texto(campo) for campo in campos]
    return any(
        _normalizar_texto(termo) in texto
        for termo in EMPRESAS_INTERNAS_GRUPO_TERMOS
        for texto in textos
    )


def filtro_empresas_internas_grupo_q():
    filtro = Q()
    for termo in EMPRESAS_INTERNAS_GRUPO_TERMOS:
        filtro |= (
            Q(nome__icontains=termo)
            | Q(razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
        )
    return filtro


def valor_km_cliente_contratual(cliente):
    valor = getattr(cliente, "valor_km", None)
    if valor not in (None, ""):
        valor = valor_km_decimal(valor)
        if valor > 0:
            return valor
    if cliente_e_empresa_interna_grupo(cliente):
        return valor_km_decimal(VALOR_KM_EMPRESA_GRUPO)
    return None


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
