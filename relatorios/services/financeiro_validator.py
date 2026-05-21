from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from relatorios.models import StatusFinanceiroItem


CENTAVO = Decimal("0.01")
DECIMAL_KM = Decimal("0.01")
DECIMAL_VALOR_KM = Decimal("0.0001")


def _decimal(valor, casas=CENTAVO):
    try:
        return Decimal(valor or "0").quantize(casas, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(valor):
    return _decimal(valor, CENTAVO)


def _km(valor):
    return _decimal(valor, DECIMAL_KM)


def _valor_km(valor):
    return _decimal(valor, DECIMAL_VALOR_KM)


def _ativo(item):
    return not (
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def _clientes_relatorio_ids(relatorio):
    ids = list(
        dict.fromkeys(relatorio.clientes_vinculados.values_list("cliente_id", flat=True))
    )
    if ids:
        return set(ids)
    return {relatorio.cliente_id} if relatorio.cliente_id else set()


def _clientes_item_ids(item):
    return set(item.clientes_vinculados.values_list("cliente_id", flat=True))


def _rateios_por_cliente(item):
    return {rateio.cliente_id: rateio for rateio in item.rateios.all()}


def _valor_positivo(valor):
    valor = _money(valor)
    return valor is not None and valor > Decimal("0.00")


def validar_cobertura_clientes_relatorio(relatorio, clientes_relatorio_ids=None):
    clientes_relatorio_ids = clientes_relatorio_ids or _clientes_relatorio_ids(relatorio)
    if not clientes_relatorio_ids:
        return []

    clientes_com_movimento = set()

    for despesa in relatorio.despesas.all():
        if not _ativo(despesa):
            continue
        clientes_item_ids = _clientes_item_ids(despesa)
        if not clientes_item_ids and len(clientes_relatorio_ids) == 1:
            clientes_item_ids = set(clientes_relatorio_ids)
        rateios = _rateios_por_cliente(despesa)
        if rateios:
            clientes_com_movimento.update(
                cliente_id
                for cliente_id, rateio in rateios.items()
                if cliente_id in clientes_item_ids
                and (
                    _valor_positivo(rateio.valor_original)
                    or _valor_positivo(rateio.valor_final)
                )
            )
        else:
            clientes_com_movimento.update(clientes_item_ids)

    for trecho in relatorio.trechos.all():
        if not _ativo(trecho):
            continue
        clientes_item_ids = _clientes_item_ids(trecho)
        if not clientes_item_ids and len(clientes_relatorio_ids) == 1:
            clientes_item_ids = set(clientes_relatorio_ids)
        calculos = _rateios_por_cliente(trecho)
        if calculos:
            clientes_com_movimento.update(
                cliente_id
                for cliente_id, calculo in calculos.items()
                if cliente_id in clientes_item_ids
                and (
                    _valor_positivo(calculo.valor_calculado)
                    or _valor_positivo(calculo.valor_final)
                )
            )
        else:
            clientes_com_movimento.update(clientes_item_ids)

    if _km(relatorio.km_excedente_interno) and _km(relatorio.km_excedente_interno) > 0:
        clientes_com_movimento.update(clientes_relatorio_ids)

    sem_movimento = clientes_relatorio_ids - clientes_com_movimento
    if sem_movimento:
        return [
            "Existem clientes no relatório sem participação em despesas ou deslocamentos."
        ]
    return []


def validar_integridade_despesa(despesa, clientes_relatorio_ids=None):
    erros = []
    clientes_relatorio_ids = clientes_relatorio_ids or _clientes_relatorio_ids(despesa.relatorio)
    clientes_item_ids = _clientes_item_ids(despesa)
    if not clientes_item_ids and len(clientes_relatorio_ids) == 1:
        clientes_item_ids = set(clientes_relatorio_ids)
    rateios = _rateios_por_cliente(despesa)

    if not clientes_item_ids:
        erros.append(f"Despesa {despesa.pk}: selecione ao menos um cliente.")

    clientes_fora = clientes_item_ids - clientes_relatorio_ids
    if clientes_fora:
        erros.append(f"Despesa {despesa.pk}: possui cliente fora do relatório.")

    rateios_fora_item = set(rateios) - clientes_item_ids
    if rateios_fora_item:
        erros.append(f"Despesa {despesa.pk}: possui rateio órfão.")

    if clientes_item_ids and set(rateios) != clientes_item_ids:
        erros.append(f"Despesa {despesa.pk}: rateio não corresponde aos clientes do item.")

    valor_original = _money(despesa.valor)
    valor_final = _money(despesa.valor_final)
    if valor_original is None or valor_original < 0:
        erros.append(f"Despesa {despesa.pk}: valor solicitado inválido.")
    if valor_final is None or valor_final < 0:
        erros.append(f"Despesa {despesa.pk}: valor aprovado inválido.")

    valores_originais_rateio = [_money(r.valor_original) for r in rateios.values()]
    valores_finais_rateio = [_money(r.valor_final) for r in rateios.values()]
    soma_original = sum(
        (valor for valor in valores_originais_rateio if valor is not None),
        Decimal("0.00"),
    )
    soma_final = sum(
        (valor for valor in valores_finais_rateio if valor is not None),
        Decimal("0.00"),
    )
    if any(valor is None for valor in valores_originais_rateio):
        erros.append(f"Despesa {despesa.pk}: possui rateio solicitado inválido.")
    if any(valor is None for valor in valores_finais_rateio):
        erros.append(f"Despesa {despesa.pk}: possui rateio aprovado inválido.")

    if valor_original is not None and rateios and soma_original != valor_original:
        erros.append(
            f"Despesa {despesa.pk}: rateio solicitado não fecha com o valor original."
        )
    if valor_final is not None and rateios and soma_final != valor_final:
        erros.append(
            f"Despesa {despesa.pk}: rateio aprovado não fecha com o valor final."
        )
    if not _ativo(despesa) and soma_final != Decimal("0.00"):
        erros.append(f"Despesa {despesa.pk}: item rejeitado possui valor aprovado rateado.")

    return erros


def validar_integridade_trecho(trecho, clientes_relatorio_ids=None):
    erros = []
    clientes_relatorio_ids = clientes_relatorio_ids or _clientes_relatorio_ids(trecho.relatorio)
    clientes_item_ids = _clientes_item_ids(trecho)
    if not clientes_item_ids and len(clientes_relatorio_ids) == 1:
        clientes_item_ids = set(clientes_relatorio_ids)
    calculos = _rateios_por_cliente(trecho)

    if not clientes_item_ids:
        erros.append(f"Trecho KM {trecho.pk}: selecione ao menos um cliente.")

    clientes_fora = clientes_item_ids - clientes_relatorio_ids
    if clientes_fora:
        erros.append(f"Trecho KM {trecho.pk}: possui cliente fora do relatório.")

    rateios_fora_item = set(calculos) - clientes_item_ids
    if rateios_fora_item:
        erros.append(f"Trecho KM {trecho.pk}: possui cálculo órfão.")

    if clientes_item_ids and set(calculos) != clientes_item_ids:
        erros.append(f"Trecho KM {trecho.pk}: cálculo não corresponde aos clientes do item.")

    if _km(trecho.km) is None or _km(trecho.km) <= 0:
        erros.append(f"Trecho KM {trecho.pk}: quilometragem inválida.")

    for calculo in calculos.values():
        km_cliente = _km(calculo.km_cliente)
        valor_km = _valor_km(calculo.valor_km)
        valor_calculado = _money(calculo.valor_calculado)
        valor_final = _money(calculo.valor_final)

        if km_cliente is None or km_cliente <= 0:
            erros.append(f"Trecho KM {trecho.pk}: KM inválido para {calculo.cliente.nome}.")
        if valor_km is None or valor_km <= 0:
            erros.append(
                f"Trecho KM {trecho.pk}: valor/KM inválido para {calculo.cliente.nome}."
            )
        if valor_calculado is None or valor_calculado < 0:
            erros.append(
                f"Trecho KM {trecho.pk}: valor automático inválido para {calculo.cliente.nome}."
            )
        if valor_final is None or valor_final < 0:
            erros.append(
                f"Trecho KM {trecho.pk}: valor final inválido para {calculo.cliente.nome}."
            )
        if km_cliente is not None and valor_km is not None and valor_calculado is not None:
            esperado = _money(km_cliente * valor_km)
            if valor_calculado != esperado:
                erros.append(
                    f"Trecho KM {trecho.pk}: cálculo automático inconsistente para {calculo.cliente.nome}."
                )

    valores_finais = [_money(r.valor_final) for r in calculos.values()]
    soma_final = sum(
        (valor for valor in valores_finais if valor is not None),
        Decimal("0.00"),
    )
    if not _ativo(trecho) and soma_final != Decimal("0.00"):
        erros.append(f"Trecho KM {trecho.pk}: item rejeitado possui valor aprovado.")

    return erros


def validar_integridade_km_excedente(relatorio, clientes_relatorio_ids=None):
    erros = []
    clientes_relatorio_ids = clientes_relatorio_ids or _clientes_relatorio_ids(relatorio)
    km_excedente = _km(relatorio.km_excedente_interno)
    if km_excedente is None:
        erros.append("KM excedente / deslocamento interno invÃ¡lido.")
        return erros
    if km_excedente < 0:
        erros.append("KM excedente / deslocamento interno nÃ£o pode ser negativo.")
    if km_excedente <= 0:
        return erros
    if not clientes_relatorio_ids:
        erros.append("KM excedente requer ao menos um cliente no relatÃ³rio.")
        return erros

    rateios = relatorio.rateio_km_excedente_clientes()
    if len(rateios) != len(clientes_relatorio_ids):
        erros.append("KM excedente nÃ£o foi distribuÃ­do para todos os clientes.")

    soma_km = sum((_km(linha["km"]) for linha in rateios), Decimal("0.00"))
    if soma_km != km_excedente:
        erros.append("Rateio de KM excedente nÃ£o fecha com o KM informado.")

    for linha in rateios:
        valor_km = _valor_km(linha["valor_km"])
        if valor_km is None or valor_km < 0:
            erros.append(
                f"KM excedente: valor/KM invÃ¡lido para {linha['cliente'].nome}."
            )
    return erros


def validar_integridade_financeira_relatorio(relatorio):
    erros = []
    clientes_relatorio_ids = _clientes_relatorio_ids(relatorio)
    if not clientes_relatorio_ids:
        erros.append("Selecione ao menos um cliente para o relatório.")

    for despesa in relatorio.despesas.all():
        erros.extend(validar_integridade_despesa(despesa, clientes_relatorio_ids))

    for trecho in relatorio.trechos.all():
        erros.extend(validar_integridade_trecho(trecho, clientes_relatorio_ids))

    erros.extend(validar_integridade_km_excedente(relatorio, clientes_relatorio_ids))
    erros.extend(validar_cobertura_clientes_relatorio(relatorio, clientes_relatorio_ids))

    total_aprovado = _money(relatorio.total_aprovado)
    if total_aprovado is None or total_aprovado < 0:
        erros.append("Total aprovado do relatório está inválido.")

    return erros
