from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

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


def _decimal(valor):
    return _money(valor)


def _data_iso(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _formatar_data(valor):
    data = _data_iso(valor)
    return data.strftime("%d/%m/%Y") if data else ""


def _formatar_datetime(valor):
    if not valor:
        return ""
    if isinstance(valor, datetime):
        dt = valor
    else:
        texto = str(valor).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(texto)
        except ValueError:
            return str(valor)
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%d/%m/%Y %H:%M")


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


def _badge_snapshot(rejeitado, valor_solicitado, valor_aprovado):
    if rejeitado:
        return "Rejeitado", "danger"
    if _money(valor_solicitado) != _money(valor_aprovado):
        return "Ajustado", "warning"
    return "Aprovado", "success"


def _cliente_nome(cliente):
    return getattr(cliente, "nome", None) or "Nao informado"


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _snapshot_distribuicao(payload):
    distribuicao = payload.get("distribuicao_clientes") or {}
    clientes = []
    for resumo in distribuicao.get("clientes") or []:
        cliente = resumo.get("cliente") or {}
        clientes.append(
            _ns(
                cliente=_ns(nome=cliente.get("nome") or "Nao informado"),
                km_total=_decimal(resumo.get("km_total")),
                valor_km_solicitado=_decimal(resumo.get("valor_km_solicitado")),
                despesas_solicitadas=_decimal(resumo.get("despesas_solicitadas")),
                total_solicitado=_decimal(resumo.get("total_solicitado")),
                total_aprovado=_decimal(resumo.get("total_aprovado")),
                diferenca_removida=_decimal(resumo.get("diferenca_removida")),
                itens_rejeitados=resumo.get("itens_rejeitados") or 0,
                status_financeiro=resumo.get("status_financeiro") or "Sem itens",
                tem_divergencia=bool(resumo.get("tem_divergencia")),
            )
        )
    return {
        "clientes": clientes,
        "total": distribuicao.get("total") or len(clientes),
        "erros": list(distribuicao.get("erros") or []),
    }


def _badge_historico(tipo_evento):
    return {
        "criado": "secondary",
        "enviado": "primary",
        "reenviado": "primary",
        "ajuste_solicitado": "warning",
        "aprovado": "success",
        "rejeitado": "danger",
        "item_rejeitado": "danger",
        "item_reativado": "info",
        "valor_alterado": "info",
    }.get(tipo_evento, "secondary")


def _historicos_snapshot(payload):
    historicos = []
    for historico in payload.get("historico") or []:
        usuario = historico.get("usuario") or {}
        historicos.append(
            _ns(
                acao=historico.get("acao") or historico.get("tipo_evento_label") or "",
                descricao=historico.get("descricao") or "",
                data_hora_display=_formatar_datetime(historico.get("data_hora")),
                usuario_nome=usuario.get("nome") or usuario.get("username") or "",
                badge_cor=_badge_historico(historico.get("tipo_evento")),
                tipo_evento_label=historico.get("tipo_evento_label") or "",
            )
        )
    return historicos


def _itens_snapshot(payload):
    itens = []
    for despesa in payload.get("despesas") or []:
        rateios = despesa.get("rateios") or []
        if rateios:
            for rateio in rateios:
                status, badge = _badge_snapshot(
                    despesa.get("rejeitado"),
                    rateio.get("valor_original"),
                    rateio.get("valor_final"),
                )
                itens.append(
                    ItemConsultaRelatorioDTO(
                        tipo="Despesa",
                        cliente=rateio.get("cliente_nome") or "Nao informado",
                        descricao=despesa.get("descricao") or "",
                        valor_solicitado=_money(rateio.get("valor_original")),
                        valor_aprovado=_money(rateio.get("valor_final")),
                        status=status,
                        badge=badge,
                        data=_data_iso(despesa.get("data")),
                    )
                )
        else:
            status, badge = _badge_snapshot(
                despesa.get("rejeitado"),
                despesa.get("valor_solicitado"),
                despesa.get("valor_final"),
            )
            itens.append(
                ItemConsultaRelatorioDTO(
                    tipo="Despesa",
                    cliente=(payload.get("clientes") or [{}])[0].get("nome", "Nao informado"),
                    descricao=despesa.get("descricao") or "",
                    valor_solicitado=_money(despesa.get("valor_solicitado")),
                    valor_aprovado=_money(despesa.get("valor_final")),
                    status=status,
                    badge=badge,
                    data=_data_iso(despesa.get("data")),
                )
            )

    for trecho in payload.get("trechos_km") or []:
        rateios = trecho.get("rateios") or []
        descricao = trecho.get("descricao") or f"{trecho.get('origem', '')} -> {trecho.get('destino', '')}"
        if rateios:
            for rateio in rateios:
                status, badge = _badge_snapshot(
                    trecho.get("rejeitado"),
                    rateio.get("valor_calculado"),
                    rateio.get("valor_final"),
                )
                itens.append(
                    ItemConsultaRelatorioDTO(
                        tipo="KM",
                        cliente=rateio.get("cliente_nome") or "Nao informado",
                        descricao=descricao,
                        valor_solicitado=_money(rateio.get("valor_calculado")),
                        valor_aprovado=_money(rateio.get("valor_final")),
                        status=status,
                        badge=badge,
                        data=_data_iso(trecho.get("data")),
                    )
                )
        else:
            status, badge = _badge_snapshot(
                trecho.get("rejeitado"),
                trecho.get("valor_calculado"),
                trecho.get("valor_final"),
            )
            itens.append(
                ItemConsultaRelatorioDTO(
                    tipo="KM",
                    cliente=(payload.get("clientes") or [{}])[0].get("nome", "Nao informado"),
                    descricao=descricao,
                    valor_solicitado=_money(trecho.get("valor_calculado")),
                    valor_aprovado=_money(trecho.get("valor_final")),
                    status=status,
                    badge=badge,
                    data=_data_iso(trecho.get("data")),
                )
            )

    return sorted(itens, key=lambda item: (item.data is None, item.data, item.tipo, item.cliente))


def _montar_consulta_snapshot(snapshot):
    payload = snapshot.payload or {}
    relatorio = payload.get("relatorio") or {}
    assinatura = payload.get("assinatura_temporal") or {}
    totais = payload.get("totais") or {}
    contagens = payload.get("contagens") or {}
    data_inicio = relatorio.get("data_inicio")
    data_fim = relatorio.get("data_fim")

    return {
        "usa_snapshot": True,
        "snapshot_checksum": snapshot.checksum,
        "clientes": [_ns(**cliente) for cliente in payload.get("clientes") or []],
        "tecnicos": [_ns(**tecnico) for tecnico in payload.get("tecnicos") or []],
        "periodo": _ns(
            inicio=_formatar_data(data_inicio),
            fim=_formatar_data(data_fim),
            mesmo_dia=data_inicio == data_fim,
        ),
        "finalizado_em": _formatar_datetime(assinatura.get("finalizado_em")),
        "totais": _ns(
            total_solicitado=_decimal(totais.get("total_solicitado")),
            total_aprovado=_decimal(totais.get("total_aprovado")),
            diferenca_removida=_decimal(totais.get("diferenca_removida")),
            total_km_percorrido=Decimal(str(totais.get("total_km_percorrido") or "0.00")),
            valor_adiantamento=_decimal(totais.get("valor_adiantamento")),
            saldo_aprovado=_decimal(totais.get("saldo_aprovado")),
        ),
        "contagens": _ns(
            despesas=contagens.get("despesas") or 0,
            trechos_km=contagens.get("trechos_km") or 0,
            itens_rejeitados=contagens.get("itens_rejeitados") or 0,
        ),
        "distribuicao_clientes": _snapshot_distribuicao(payload),
        "historicos": _historicos_snapshot(payload),
        "itens": _itens_snapshot(payload),
        "anexos": [
            AnexoConsultaRelatorioDTO(
                tipo=anexo.get("tipo") or "Anexo",
                descricao=anexo.get("descricao") or "",
                url=anexo.get("url") or "",
                nome=anexo.get("nome") or "",
            )
            for anexo in payload.get("anexos") or []
        ],
        "observacoes": [
            (obs.get("titulo") or "Observacao", obs.get("texto") or "")
            for obs in payload.get("observacoes") or []
        ],
        "total_itens_rejeitados": contagens.get("itens_rejeitados") or 0,
    }


def _montar_consulta_viva(relatorio):
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
        "usa_snapshot": False,
        "clientes": [_ns(nome=cliente.nome) for cliente in relatorio.clientes_exibicao()],
        "tecnicos": [_ns(nome=tecnico.nome) for tecnico in relatorio.tecnicos_exibicao()],
        "periodo": _ns(
            inicio=_formatar_data(relatorio.data_inicio),
            fim=_formatar_data(relatorio.data_fim),
            mesmo_dia=relatorio.data_inicio == relatorio.data_fim,
        ),
        "finalizado_em": _formatar_datetime(relatorio.aprovado_em),
        "totais": _ns(
            total_solicitado=relatorio.total_solicitado,
            total_aprovado=relatorio.total_aprovado,
            diferenca_removida=relatorio.diferenca_removida,
            total_km_percorrido=relatorio.total_km_percorrido,
            valor_adiantamento=relatorio.valor_adiantamento,
            saldo_aprovado=relatorio.saldo_aprovado,
        ),
        "contagens": _ns(
            despesas=relatorio.despesas.count(),
            trechos_km=relatorio.trechos.count(),
            itens_rejeitados=sum(1 for item in itens if item.badge == "danger"),
        ),
        "distribuicao_clientes": resumo_financeiro_por_cliente(relatorio),
        "historicos": [
            _ns(
                acao=historico.acao,
                descricao=historico.descricao,
                data_hora_display=_formatar_datetime(historico.data_hora),
                usuario_nome=(
                    historico.usuario.get_full_name() or historico.usuario.username
                    if historico.usuario
                    else ""
                ),
                badge_cor=historico.badge_cor,
                tipo_evento_label=historico.get_tipo_evento_display(),
            )
            for historico in relatorio.historicos.all()
        ],
        "itens": itens,
        "anexos": anexos,
        "observacoes": observacoes,
        "total_itens_rejeitados": sum(1 for item in itens if item.badge == "danger"),
    }


def montar_consulta_relatorio(relatorio):
    try:
        snapshot = relatorio.snapshot_financeiro
    except ObjectDoesNotExist:
        snapshot = None
    if snapshot:
        return _montar_consulta_snapshot(snapshot)
    return _montar_consulta_viva(relatorio)
