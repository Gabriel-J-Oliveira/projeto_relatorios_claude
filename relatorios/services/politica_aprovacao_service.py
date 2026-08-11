import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


logger = logging.getLogger(__name__)

CENTAVO = Decimal("0.01")


def _money(valor):
    if valor is None or valor == "":
        return None
    try:
        return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def valor_aprovado_inicial_por_politica(despesa):
    valor = _money(getattr(despesa, "valor", None))
    limite = _money(getattr(despesa, "valor_politica", None))
    if valor is None or limite is None:
        return None
    if valor > limite:
        return limite
    return None


def aplicar_politica_valor_aprovado_inicial(despesa, preservar_manual=True):
    valor_anterior = getattr(despesa, "valor_aprovado", None)
    valor_novo = valor_aprovado_inicial_por_politica(despesa)
    limite_atual = _money(getattr(despesa, "valor_politica", None))
    valor_anterior_money = _money(valor_anterior)
    if (
        preservar_manual
        and valor_anterior_money is not None
        and limite_atual is not None
        and valor_anterior_money != limite_atual
    ):
        logger.info(
            "POLITICA_APROVADO_INICIAL_PRESERVADO relatorio=%s despesa=%s valor=%s limite=%s valor_aprovado=%s",
            getattr(despesa, "relatorio_id", None),
            getattr(despesa, "pk", None),
            getattr(despesa, "valor", None),
            limite_atual,
            valor_anterior,
        )
        return False
    if valor_anterior == valor_novo:
        return False

    despesa.valor_aprovado = valor_novo
    despesa.save(update_fields=["valor_aprovado"])
    logger.info(
        "POLITICA_APROVADO_INICIAL relatorio=%s despesa=%s valor=%s limite=%s valor_aprovado_anterior=%s valor_aprovado_novo=%s tecnicos=%s",
        getattr(despesa, "relatorio_id", None),
        getattr(despesa, "pk", None),
        getattr(despesa, "valor", None),
        getattr(despesa, "valor_politica", None),
        valor_anterior,
        valor_novo,
        getattr(despesa, "quantidade_tecnicos_participantes", 1),
    )
    return True
