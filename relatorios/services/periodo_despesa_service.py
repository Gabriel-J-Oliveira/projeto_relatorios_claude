from datetime import date

from relatorios.models import TipoDespesa


def calcular_diarias_periodo(data_inicio, data_fim):
    if not data_inicio or not data_fim:
        return 0
    if not isinstance(data_inicio, date) or not isinstance(data_fim, date):
        return 0
    dias = (data_fim - data_inicio).days
    return max(dias, 0)


def despesa_usa_periodo(tipo_despesa):
    return tipo_despesa == TipoDespesa.HOSPEDAGEM
