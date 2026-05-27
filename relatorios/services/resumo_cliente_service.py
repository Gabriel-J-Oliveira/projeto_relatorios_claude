from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


CENTAVO = Decimal("0.01")


def _money(valor):
    return Decimal(valor or "0").quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _dividir_decimal(total, cliente_ids):
    total = _money(total)
    if not cliente_ids:
        return {}
    base = (total / Decimal(len(cliente_ids))).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    valores = {cliente_id: base for cliente_id in cliente_ids}
    ultimo = cliente_ids[-1]
    valores[ultimo] = (total - sum(
        (valor for cid, valor in valores.items() if cid != ultimo),
        Decimal("0.00"),
    )).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return valores


@dataclass
class ClienteResumoFinanceiroDTO:
    cliente: object
    motivo_viagem: str = ""
    km_total: Decimal = Decimal("0.00")
    valor_km_solicitado: Decimal = Decimal("0.00")
    valor_km_reembolso_tecnico: Decimal = Decimal("0.00")
    excesso_reducao_km: Decimal = Decimal("0.00")
    despesas_solicitadas: Decimal = Decimal("0.00")
    total_solicitado: Decimal = Decimal("0.00")
    total_aprovado: Decimal = Decimal("0.00")
    diferenca_removida: Decimal = Decimal("0.00")
    itens_rejeitados: int = 0
    status_financeiro: str = "Sem itens"
    tem_divergencia: bool = False


def _clientes_despesa(despesa, clientes_relatorio_ids):
    ids = list(
        dict.fromkeys(despesa.clientes_vinculados.values_list("cliente_id", flat=True))
    )
    return ids or clientes_relatorio_ids


def _clientes_trecho(trecho, clientes_relatorio_ids):
    ids = list(
        dict.fromkeys(trecho.clientes_vinculados.values_list("cliente_id", flat=True))
    )
    return ids or clientes_relatorio_ids


def _marcar_status(resumo):
    resumo.total_solicitado = _money(
        resumo.despesas_solicitadas + resumo.valor_km_solicitado
    )
    resumo.total_aprovado = _money(resumo.total_aprovado)
    resumo.diferenca_removida = _money(
        resumo.total_solicitado - resumo.total_aprovado
        if resumo.total_solicitado > resumo.total_aprovado
        else Decimal("0.00")
    )
    resumo.tem_divergencia = resumo.diferenca_removida > 0 or resumo.itens_rejeitados > 0
    if resumo.total_solicitado == 0:
        resumo.status_financeiro = "Sem itens"
    elif resumo.total_aprovado == 0 and resumo.itens_rejeitados > 0:
        resumo.status_financeiro = "Removido do reembolso"
    elif resumo.tem_divergencia:
        resumo.status_financeiro = "Com ajustes"
    else:
        resumo.status_financeiro = "Aprovado integral"


def resumo_financeiro_por_cliente(relatorio):
    clientes = list(relatorio.clientes_exibicao())
    clientes_por_id = {cliente.pk: cliente for cliente in clientes}
    motivos_por_cliente = {
        vinculo.cliente_id: vinculo.motivo_viagem or ""
        for vinculo in relatorio.clientes_vinculados.all()
    }

    for despesa in relatorio.despesas.all():
        for vinculo in despesa.clientes_vinculados.select_related("cliente"):
            clientes_por_id.setdefault(vinculo.cliente_id, vinculo.cliente)
        for rateio in despesa.rateios.select_related("cliente"):
            clientes_por_id.setdefault(rateio.cliente_id, rateio.cliente)

    for trecho in relatorio.trechos.all():
        for vinculo in trecho.clientes_vinculados.select_related("cliente"):
            clientes_por_id.setdefault(vinculo.cliente_id, vinculo.cliente)
        for calculo in trecho.rateios.select_related("cliente"):
            clientes_por_id.setdefault(calculo.cliente_id, calculo.cliente)

    if not clientes_por_id and relatorio.cliente_id:
        clientes_por_id[relatorio.cliente_id] = relatorio.cliente

    resumos = {
        cliente_id: ClienteResumoFinanceiroDTO(
            cliente=cliente,
            motivo_viagem=motivos_por_cliente.get(cliente_id, ""),
        )
        for cliente_id, cliente in clientes_por_id.items()
    }
    clientes_relatorio_ids = list(resumos.keys())

    for despesa in relatorio.despesas.all():
        cliente_ids = _clientes_despesa(despesa, clientes_relatorio_ids)
        if not cliente_ids:
            continue
        solicitados = _dividir_decimal(despesa.valor, cliente_ids)
        aprovados = {
            rateio.cliente_id: _money(rateio.valor_final)
            for rateio in despesa.rateios.all()
        }
        for cliente_id in cliente_ids:
            resumo = resumos.get(cliente_id)
            if not resumo:
                continue
            resumo.despesas_solicitadas += solicitados.get(cliente_id, Decimal("0.00"))
            resumo.total_aprovado += aprovados.get(
                cliente_id,
                Decimal("0.00") if despesa.rejeitado or despesa.status_financeiro == "rejeitado" else solicitados.get(cliente_id, Decimal("0.00")),
            )
            if despesa.rejeitado or despesa.status_financeiro == "rejeitado":
                resumo.itens_rejeitados += 1

    for trecho in relatorio.trechos.all():
        cliente_ids = _clientes_trecho(trecho, clientes_relatorio_ids)
        calculos = {calculo.cliente_id: calculo for calculo in trecho.rateios.all()}
        for cliente_id in cliente_ids:
            resumo = resumos.get(cliente_id)
            if not resumo:
                continue
            calculo = calculos.get(cliente_id)
            if calculo:
                resumo.km_total += calculo.km_cliente
                resumo.valor_km_solicitado += _money(calculo.valor_calculado)
                resumo.valor_km_reembolso_tecnico += _money(calculo.valor_reembolso_tecnico)
                resumo.total_aprovado += _money(calculo.valor_final)
            elif not trecho.rejeitado and trecho.status_financeiro != "rejeitado":
                resumo.km_total += trecho.km
                resumo.valor_km_solicitado += _money(trecho.valor_calculado)
                resumo.valor_km_reembolso_tecnico += _money(trecho.valor_reembolso_tecnico)
                resumo.total_aprovado += _money(trecho.valor_final)
            if trecho.rejeitado or trecho.status_financeiro == "rejeitado":
                resumo.itens_rejeitados += 1

    for linha in relatorio.rateio_km_excedente_clientes():
        cliente = linha["cliente"]
        resumo = resumos.get(cliente.pk)
        if not resumo:
            resumo = ClienteResumoFinanceiroDTO(
                cliente=cliente,
                motivo_viagem=motivos_por_cliente.get(cliente.pk, ""),
            )
            resumos[cliente.pk] = resumo
        resumo.km_total += linha["km"]
        resumo.valor_km_solicitado += _money(linha["valor_calculado"])
        resumo.valor_km_reembolso_tecnico += _money(linha["valor_reembolso_tecnico"])
        resumo.total_aprovado += _money(linha["valor_calculado"])

    for resumo in resumos.values():
        resumo.km_total = Decimal(resumo.km_total or "0").quantize(Decimal("0.01"))
        resumo.despesas_solicitadas = _money(resumo.despesas_solicitadas)
        resumo.valor_km_solicitado = _money(resumo.valor_km_solicitado)
        resumo.valor_km_reembolso_tecnico = _money(resumo.valor_km_reembolso_tecnico)
        resumo.excesso_reducao_km = _money(
            resumo.valor_km_solicitado - resumo.valor_km_reembolso_tecnico
        )
        _marcar_status(resumo)

    lista = sorted(resumos.values(), key=lambda resumo: resumo.cliente.nome)
    total_solicitado = _money(sum((r.total_solicitado for r in lista), Decimal("0.00")))
    total_aprovado = _money(sum((r.total_aprovado for r in lista), Decimal("0.00")))
    erros = []
    if total_solicitado != _money(relatorio.total_solicitado):
        erros.append("Distribuição por cliente não fecha com o total solicitado global.")
    if total_aprovado != _money(relatorio.total_aprovado):
        erros.append("Distribuição por cliente não fecha com o total aprovado global.")

    return {
        "clientes": lista,
        "total": len(lista),
        "erros": erros,
    }
