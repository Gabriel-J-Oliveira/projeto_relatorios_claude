from dataclasses import dataclass
from decimal import Decimal

from relatorios.models import StatusFinanceiroItem
from relatorios.services.resumo_cliente_service import resumo_financeiro_por_cliente


@dataclass(frozen=True)
class ItemConsultaRelatorioDTO:
    tipo: str
    cliente: str
    descricao: str
    valor_solicitado: Decimal
    valor_aprovado: Decimal
    status: str
    badge: str
    data: object = None


@dataclass(frozen=True)
class AnexoConsultaRelatorioDTO:
    tipo: str
    descricao: str
    url: str
    nome: str


def _money(valor):
    return Decimal(valor or "0.00").quantize(Decimal("0.01"))


def _item_rejeitado(item):
    return (
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def _badge_item(item, valor_solicitado, valor_aprovado):
    if _item_rejeitado(item):
        return "Rejeitado", "danger"
    if _money(valor_solicitado) != _money(valor_aprovado):
        return "Ajustado", "warning"
    return "Aprovado", "success"


def _cliente_nome(cliente):
    return getattr(cliente, "nome", None) or "Nao informado"


def montar_consulta_relatorio(relatorio):
    itens = []
    anexos = []

    for despesa in relatorio.despesas.all():
        rateios = list(despesa.rateios.all())
        if rateios:
            for rateio in rateios:
                status, badge = _badge_item(
                    despesa,
                    rateio.valor_original,
                    rateio.valor_final,
                )
                itens.append(
                    ItemConsultaRelatorioDTO(
                        tipo="Despesa",
                        cliente=_cliente_nome(rateio.cliente),
                        descricao=despesa.descricao,
                        valor_solicitado=_money(rateio.valor_original),
                        valor_aprovado=_money(rateio.valor_final),
                        status=status,
                        badge=badge,
                        data=despesa.data,
                    )
                )
        else:
            status, badge = _badge_item(despesa, despesa.valor, despesa.valor_final)
            itens.append(
                ItemConsultaRelatorioDTO(
                    tipo="Despesa",
                    cliente=_cliente_nome(relatorio.cliente),
                    descricao=despesa.descricao,
                    valor_solicitado=_money(despesa.valor),
                    valor_aprovado=_money(despesa.valor_final),
                    status=status,
                    badge=badge,
                    data=despesa.data,
                )
            )

        if despesa.comprovante:
            anexos.append(
                AnexoConsultaRelatorioDTO(
                    tipo="Comprovante",
                    descricao=despesa.descricao,
                    url=despesa.comprovante.url,
                    nome=despesa.comprovante.name.rsplit("/", 1)[-1],
                )
            )

    for trecho in relatorio.trechos.all():
        rateios = list(trecho.rateios.all())
        descricao = f"{trecho.origem} -> {trecho.destino}"
        if rateios:
            for calculo in rateios:
                status, badge = _badge_item(
                    trecho,
                    calculo.valor_calculado,
                    calculo.valor_final,
                )
                itens.append(
                    ItemConsultaRelatorioDTO(
                        tipo="KM",
                        cliente=_cliente_nome(calculo.cliente),
                        descricao=descricao,
                        valor_solicitado=_money(calculo.valor_calculado),
                        valor_aprovado=_money(calculo.valor_final),
                        status=status,
                        badge=badge,
                        data=trecho.data,
                    )
                )
        else:
            status, badge = _badge_item(
                trecho,
                trecho.valor_calculado,
                trecho.valor_final,
            )
            itens.append(
                ItemConsultaRelatorioDTO(
                    tipo="KM",
                    cliente=_cliente_nome(relatorio.cliente),
                    descricao=descricao,
                    valor_solicitado=_money(trecho.valor_calculado),
                    valor_aprovado=_money(trecho.valor_final),
                    status=status,
                    badge=badge,
                    data=trecho.data,
                )
            )

    itens.sort(key=lambda item: (item.data is None, item.data, item.tipo, item.cliente))

    observacoes = []
    if relatorio.observacoes:
        observacoes.append(("Observacoes gerais", relatorio.observacoes))
    if relatorio.motivo_rejeicao:
        observacoes.append(("Justificativa financeira", relatorio.motivo_rejeicao))
    for despesa in relatorio.despesas.all():
        motivo = despesa.motivo_rejeicao or despesa.motivo_recusa
        if motivo:
            observacoes.append((f"Despesa {despesa.pk}", motivo))
    for trecho in relatorio.trechos.all():
        motivo = trecho.motivo_rejeicao or trecho.motivo_recusa
        if motivo:
            observacoes.append((f"Trecho KM {trecho.pk}", motivo))

    return {
        "distribuicao_clientes": resumo_financeiro_por_cliente(relatorio),
        "itens": itens,
        "anexos": anexos,
        "observacoes": observacoes,
        "total_itens_rejeitados": sum(1 for item in itens if item.badge == "danger"),
    }
