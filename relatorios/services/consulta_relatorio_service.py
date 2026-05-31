from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import logging
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from relatorios.models import StatusFinanceiroItem, StatusRelatorio, TipoEventoHistorico
from relatorios.services.resumo_cliente_service import resumo_financeiro_por_cliente
from relatorios.services.snapshot_service import SnapshotError, validar_snapshot_payload


logger = logging.getLogger(__name__)


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
                motivo_viagem=resumo.get("motivo_viagem") or cliente.get("motivo_viagem") or "",
                km_total=_decimal(resumo.get("km_total")),
                valor_km_solicitado=_decimal(resumo.get("valor_km_solicitado")),
                valor_km_reembolso_tecnico=_decimal(
                    resumo.get("valor_km_reembolso_tecnico")
                ),
                excesso_reducao_km=_decimal(resumo.get("excesso_reducao_km")),
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


def _status_badge_cor(status):
    return {
        StatusRelatorio.RASCUNHO: "secondary",
        StatusRelatorio.CONFERENCIA: "warning",
        StatusRelatorio.AJUSTE: "orange",
        StatusRelatorio.APROVADO: "success",
        StatusRelatorio.REJEITADO: "danger",
    }.get(status, "secondary")


def _historicos_snapshot(payload):
    historicos = []
    for historico in payload.get("historico") or []:
        if not _historico_deve_exibir(
            historico.get("tipo_evento"),
            historico.get("descricao") or "",
            historico.get("dados_json") or {},
        ):
            continue
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


def _historico_deve_exibir(tipo_evento, descricao, dados_json):
    if tipo_evento != TipoEventoHistorico.VALOR_ALTERADO:
        return True

    descricao = descricao or ""
    dados_json = dados_json or {}
    valor_anterior = str(dados_json.get("valor_anterior") or "").strip()
    valor_anterior_zero = valor_anterior in {"", "0", "0.0", "0.00", "0.0000"}

    if descricao.startswith("Valor por KM aprovado do trecho"):
        return False
    if descricao.startswith("Valor aprovado da despesa") and valor_anterior_zero:
        return False
    return True


def _km_excedente_snapshot(payload):
    km_excedente = payload.get("km_excedente") or {}
    return _ns(
        km_total=_decimal(km_excedente.get("km_total")),
        observacao=km_excedente.get("observacao") or "",
        total=_decimal(km_excedente.get("total")),
        rateios=[
            _ns(
                cliente_nome=rateio.get("cliente_nome") or "Nao informado",
                km=_decimal(rateio.get("km")),
                valor_km=_decimal(rateio.get("valor_km")),
                valor_calculado=_decimal(rateio.get("valor_calculado")),
                valor_final=_decimal(rateio.get("valor_final")),
            )
            for rateio in km_excedente.get("rateios") or []
        ],
    )


def _mapa_trechos_snapshot(payload):
    dados = []
    for ordem, trecho in enumerate(payload.get("trechos_km") or [], start=1):
        origem_lat = trecho.get("origem_lat")
        origem_lon = trecho.get("origem_lon")
        destino_lat = trecho.get("destino_lat")
        destino_lon = trecho.get("destino_lon")
        if not all([origem_lat, origem_lon, destino_lat, destino_lon]):
            continue
        clientes = [
            rateio.get("cliente_nome")
            for rateio in trecho.get("rateios") or []
            if rateio.get("cliente_nome")
        ] or [
            cliente.get("nome")
            for cliente in trecho.get("clientes") or []
            if cliente.get("nome")
        ]
        dados.append(
            {
                "ordem": ordem,
                "origem": trecho.get("origem_endereco_completo") or trecho.get("origem") or "",
                "destino": trecho.get("destino_endereco_completo") or trecho.get("destino") or "",
                "origem_lat": origem_lat,
                "origem_lon": origem_lon,
                "destino_lat": destino_lat,
                "destino_lon": destino_lon,
                "rota_geojson": trecho.get("rota_geojson") or {},
                "km_calculado": trecho.get("km_calculado_api") or "",
                "km_informado": trecho.get("km_informado") or trecho.get("km") or "",
                "diferenca_percentual": trecho.get("diferenca_km_percentual") or "",
                "divergente": bool(trecho.get("km_divergente_rota")),
                "clientes": clientes,
            }
        )
    return dados


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

    km_excedente = payload.get("km_excedente") or {}
    for rateio in km_excedente.get("rateios") or []:
        itens.append(
            ItemConsultaRelatorioDTO(
                tipo="KM excedente",
                cliente=rateio.get("cliente_nome") or "Nao informado",
                descricao=km_excedente.get("observacao") or "Deslocamento interno",
                valor_solicitado=_money(rateio.get("valor_calculado")),
                valor_aprovado=_money(rateio.get("valor_final")),
                status="Aprovado",
                badge="success",
                data=None,
            )
        )

    return sorted(itens, key=lambda item: (item.data is None, item.data, item.tipo, item.cliente))


def _status_interno_snapshot(item, solicitado, aprovado):
    status, badge = _badge_snapshot(item.get("rejeitado"), solicitado, aprovado)
    return status, badge


def _rateios_despesa_snapshot(despesa, payload):
    rateios = despesa.get("rateios") or []
    if rateios:
        return [
            _ns(
                cliente=rateio.get("cliente_nome") or "Nao informado",
                valor_original=_money(rateio.get("valor_original")),
                valor_final=_money(rateio.get("valor_final")),
                status=rateio.get("status_label") or rateio.get("status") or "-",
                motivo=rateio.get("motivo_ajuste") or "",
            )
            for rateio in rateios
        ]
    clientes = despesa.get("clientes") or payload.get("clientes") or []
    return [
        _ns(
            cliente=cliente.get("nome") or "Nao informado",
            valor_original=_money(despesa.get("valor_solicitado")),
            valor_final=_money(despesa.get("valor_final")),
            status="-",
            motivo="",
        )
        for cliente in clientes[:1]
    ]


def _despesas_internas_snapshot(payload):
    linhas = []
    for despesa in payload.get("despesas") or []:
        solicitado = _money(despesa.get("valor_solicitado"))
        aprovado = Decimal("0.00") if despesa.get("rejeitado") else _money(despesa.get("valor_final"))
        status, badge = _status_interno_snapshot(despesa, solicitado, aprovado)
        linhas.append(
            _ns(
                id=despesa.get("id"),
                data=_data_iso(despesa.get("data")),
                tipo=despesa.get("tipo_label") or despesa.get("tipo") or "-",
                descricao=despesa.get("descricao") or "-",
                valor_solicitado=solicitado,
                valor_aprovado=aprovado,
                status=status,
                badge=badge,
                motivo=despesa.get("motivo_rejeicao") or despesa.get("motivo_recusa") or "",
                tipo_documento=despesa.get("tipo_documento_comprovante_label") or "",
                numero_documento=despesa.get("numero_documento_comprovante") or "",
                valor_politica=_money(despesa.get("valor_politica"))
                if despesa.get("valor_politica") is not None
                else None,
                excesso_politica=_money(despesa.get("excesso_politica")),
                acima_politica=bool(despesa.get("acima_politica")),
                politica_localidade=despesa.get("politica_descricao")
                or despesa.get("politica_localidade_label")
                or "",
                rateios=_rateios_despesa_snapshot(despesa, payload),
            )
        )
    return linhas


def _rateios_trecho_snapshot(trecho, payload):
    rateios = trecho.get("rateios") or []
    if rateios:
        return [
            _ns(
                cliente=rateio.get("cliente_nome") or "Nao informado",
                km=_money(rateio.get("km_cliente") or rateio.get("km_final")),
                valor_km=Decimal(str(rateio.get("valor_km_cliente_contratual") or rateio.get("valor_km") or "0.00")),
                valor_km_control_sul=Decimal(str(rateio.get("valor_km_control_sul") or "1.35")),
                valor_reembolso_tecnico=_money(rateio.get("valor_reembolso_tecnico")),
                excesso_reducao=_money(rateio.get("diferenca") or rateio.get("excesso_reducao")),
                valor_original=_money(rateio.get("valor_cobranca_calculado") or rateio.get("valor_calculado")),
                valor_final=_money(rateio.get("valor_cobranca_cliente") or rateio.get("valor_final")),
                status=rateio.get("status_label") or rateio.get("status") or "-",
                motivo=rateio.get("motivo_ajuste") or "",
            )
            for rateio in rateios
        ]
    clientes = trecho.get("clientes") or payload.get("clientes") or []
    return [
        _ns(
            cliente=cliente.get("nome") or "Nao informado",
            km=_money(trecho.get("km")),
            valor_km=Decimal(str(cliente.get("valor_km") or "0.00")),
            valor_km_control_sul=Decimal(
                str(trecho.get("valor_km_control_sul") or "1.35")
            ),
            valor_reembolso_tecnico=_money(trecho.get("valor_reembolso_tecnico")),
            excesso_reducao=_money(trecho.get("diferenca") or trecho.get("excesso_reducao")),
            valor_original=_money(trecho.get("valor_cobranca_calculado") or trecho.get("valor_calculado")),
            valor_final=_money(trecho.get("valor_cobranca_cliente") or trecho.get("valor_final")),
            status="-",
            motivo="",
        )
        for cliente in clientes[:1]
    ]


def _trechos_internos_snapshot(payload):
    linhas = []
    for trecho in payload.get("trechos_km") or []:
        solicitado = _money(trecho.get("valor_calculado"))
        aprovado = Decimal("0.00") if trecho.get("rejeitado") else _money(trecho.get("valor_final"))
        status, badge = _status_interno_snapshot(trecho, solicitado, aprovado)
        linhas.append(
            _ns(
                id=trecho.get("id"),
                data=_data_iso(trecho.get("data")),
                origem=trecho.get("origem") or "-",
                destino=trecho.get("destino") or "-",
                km=_money(trecho.get("km")),
                km_calculado_api=_money(trecho.get("km_calculado_api")),
                valor_solicitado=solicitado,
                valor_aprovado=aprovado,
                status=status,
                badge=badge,
                motivo=trecho.get("motivo_rejeicao") or trecho.get("motivo_recusa") or "",
                rateios=_rateios_trecho_snapshot(trecho, payload),
            )
        )
    return linhas


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
        "relatorio": _ns(
            identificador=relatorio.get("identificador") or relatorio.get("numero") or "",
            numero=relatorio.get("numero") or "",
            status=relatorio.get("status") or "",
            status_label=relatorio.get("status_label") or "",
            status_badge_cor=_status_badge_cor(relatorio.get("status") or ""),
        ),
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
        "km_excedente": _km_excedente_snapshot(payload),
        "mapa_trechos": _mapa_trechos_snapshot(payload),
        "historicos": _historicos_snapshot(payload),
        "despesas_internas": _despesas_internas_snapshot(payload),
        "trechos_internos": _trechos_internos_snapshot(payload),
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


def _km_excedente_vivo(relatorio):
    return _ns(
        km_total=relatorio.km_excedente_interno or Decimal("0.00"),
        observacao=relatorio.observacao_km_excedente or "",
        total=relatorio.total_km_excedente,
        rateios=[
            _ns(
                cliente_nome=linha["cliente"].nome,
                km=linha["km"],
                valor_km=linha["valor_km"],
                valor_calculado=linha["valor_calculado"],
                valor_final=linha["valor_calculado"],
            )
            for linha in relatorio.rateio_km_excedente_clientes()
        ],
    )


def _mapa_trechos_vivo(relatorio):
    dados = []
    for ordem, trecho in enumerate(relatorio.trechos.all(), start=1):
        if not all([trecho.origem_lat, trecho.origem_lon, trecho.destino_lat, trecho.destino_lon]):
            continue
        clientes = [
            rateio.cliente.nome
            for rateio in trecho.rateios.all()
        ] or [
            vinculo.cliente.nome
            for vinculo in trecho.clientes_vinculados.all()
        ]
        dados.append(
            {
                "ordem": ordem,
                "origem": trecho.origem_endereco_completo or trecho.origem,
                "destino": trecho.destino_endereco_completo or trecho.destino,
                "origem_lat": str(trecho.origem_lat),
                "origem_lon": str(trecho.origem_lon),
                "destino_lat": str(trecho.destino_lat),
                "destino_lon": str(trecho.destino_lon),
                "rota_geojson": trecho.rota_geojson or {},
                "km_calculado": str(trecho.km_calculado_api or ""),
                "km_informado": str(trecho.km_informado or trecho.km or ""),
                "diferenca_percentual": str(trecho.diferenca_km_percentual or ""),
                "divergente": trecho.km_divergente_rota,
                "clientes": clientes,
            }
        )
    return dados


def _despesas_internas_vivas(relatorio):
    linhas = []
    for despesa in relatorio.despesas.all():
        solicitado = _money(despesa.valor)
        aprovado = Decimal("0.00") if _item_rejeitado(despesa) else _money(despesa.valor_final)
        status, badge = _badge_item(despesa, solicitado, aprovado)
        rateios = [
            _ns(
                cliente=rateio.cliente.nome,
                valor_original=_money(rateio.valor_original),
                valor_final=_money(rateio.valor_final),
                status=rateio.get_status_display(),
                motivo=rateio.motivo_ajuste or "",
            )
            for rateio in despesa.rateios.all()
        ]
        if not rateios:
            vinculos = list(despesa.clientes_vinculados.select_related("cliente").all())
            rateios = [
                _ns(cliente=vinculo.cliente.nome, valor_original=solicitado, valor_final=aprovado, status="-", motivo="")
                for vinculo in vinculos[:1]
            ] or [_ns(cliente=_cliente_nome(relatorio.cliente), valor_original=solicitado, valor_final=aprovado, status="-", motivo="")]
        linhas.append(
            _ns(
                id=despesa.pk,
                data=despesa.data,
                tipo=despesa.get_tipo_display(),
                descricao=despesa.descricao,
                valor_solicitado=solicitado,
                valor_aprovado=aprovado,
                status=status,
                badge=badge,
                motivo=despesa.motivo_rejeicao or despesa.motivo_recusa or "",
                tipo_documento=despesa.get_tipo_documento_comprovante_display()
                if despesa.tipo_documento_comprovante
                else "",
                numero_documento=despesa.numero_documento_comprovante,
                valor_politica=_money(despesa.valor_politica)
                if despesa.valor_politica is not None
                else None,
                excesso_politica=_money(despesa.excesso_politica),
                acima_politica=bool(despesa.acima_politica),
                politica_localidade=despesa.politica_localidade_label,
                rateios=rateios,
            )
        )
    return linhas


def _trechos_internos_vivos(relatorio):
    linhas = []
    for trecho in relatorio.trechos.all():
        solicitado = _money(trecho.valor_calculado_clientes)
        aprovado = Decimal("0.00") if _item_rejeitado(trecho) else _money(trecho.valor_final_clientes)
        status, badge = _badge_item(trecho, solicitado, aprovado)
        rateios = [
            _ns(
                cliente=rateio.cliente.nome,
                km=_money(rateio.km_cliente),
                valor_km=rateio.valor_km,
                valor_km_control_sul=rateio.valor_km_control_sul,
                valor_reembolso_tecnico=_money(rateio.valor_reembolso_tecnico),
                excesso_reducao=_money(rateio.excesso_reducao),
                valor_original=_money(rateio.valor_calculado),
                valor_final=_money(rateio.valor_final),
                status=rateio.get_status_display(),
                motivo=rateio.motivo_ajuste or "",
            )
            for rateio in trecho.rateios.all()
        ]
        if not rateios:
            vinculos = list(trecho.clientes_vinculados.select_related("cliente").all())
            rateios = [
                _ns(cliente=vinculo.cliente.nome, km=_money(trecho.km), valor_km=vinculo.cliente.valor_km or Decimal("0.00"), valor_km_control_sul=trecho.valor_km_control_sul, valor_reembolso_tecnico=_money(trecho.valor_reembolso_tecnico), excesso_reducao=_money(trecho.excesso_reducao_km), valor_original=solicitado, valor_final=aprovado, status="-", motivo="")
                for vinculo in vinculos[:1]
            ] or [_ns(cliente=_cliente_nome(relatorio.cliente), km=_money(trecho.km), valor_km=getattr(relatorio.cliente, "valor_km", None) or Decimal("0.00"), valor_km_control_sul=trecho.valor_km_control_sul, valor_reembolso_tecnico=_money(trecho.valor_reembolso_tecnico), excesso_reducao=_money(trecho.excesso_reducao_km), valor_original=solicitado, valor_final=aprovado, status="-", motivo="")]
        linhas.append(
            _ns(
                id=trecho.pk,
                data=trecho.data,
                origem=trecho.origem,
                destino=trecho.destino,
                km=_money(trecho.km),
                km_calculado_api=_money(trecho.km_calculado_api),
                valor_solicitado=solicitado,
                valor_aprovado=aprovado,
                status=status,
                badge=badge,
                motivo=trecho.motivo_rejeicao or trecho.motivo_recusa or "",
                rateios=rateios,
            )
        )
    return linhas


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
                trecho.valor_calculado_clientes,
                trecho.valor_final_clientes,
            )
            itens.append(
                ItemConsultaRelatorioDTO(
                    tipo="KM",
                    cliente=_cliente_nome(relatorio.cliente),
                    descricao=descricao,
                    valor_solicitado=_money(trecho.valor_calculado_clientes),
                    valor_aprovado=_money(trecho.valor_final_clientes),
                    status=status,
                    badge=badge,
                    data=trecho.data,
                )
            )

    for linha in relatorio.rateio_km_excedente_clientes():
        itens.append(
            ItemConsultaRelatorioDTO(
                tipo="KM excedente",
                cliente=_cliente_nome(linha["cliente"]),
                descricao=relatorio.observacao_km_excedente or "Deslocamento interno",
                valor_solicitado=_money(linha["valor_calculado"]),
                valor_aprovado=_money(linha["valor_calculado"]),
                status="Aprovado",
                badge="success",
                data=None,
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
        "relatorio": _ns(
            identificador=relatorio.identificador,
            numero=relatorio.numero or "",
            status=relatorio.status,
            status_label=relatorio.get_status_display(),
            status_badge_cor=relatorio.status_badge_cor,
        ),
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
        "km_excedente": _km_excedente_vivo(relatorio),
        "mapa_trechos": _mapa_trechos_vivo(relatorio),
        "despesas_internas": _despesas_internas_vivas(relatorio),
        "trechos_internos": _trechos_internos_vivos(relatorio),
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
            if _historico_deve_exibir(
                historico.tipo_evento,
                historico.descricao,
                historico.dados_json or {},
            )
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
        try:
            validar_snapshot_payload(snapshot.payload or {})
        except SnapshotError as exc:
            logger.error(
                "Snapshot financeiro invalido para relatorio %s. Usando fallback legado. Erros: %s",
                relatorio.pk,
                exc,
            )
        else:
            logger.debug(
                "Consulta final do relatorio %s renderizada a partir do snapshot financeiro %s.",
                relatorio.pk,
                snapshot.pk,
            )
            return _montar_consulta_snapshot(snapshot)
    elif relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        logger.warning(
            "Relatorio finalizado %s (%s) sem snapshot financeiro. Usando fallback legado para compatibilidade.",
            relatorio.pk,
            relatorio.status,
        )
    return _montar_consulta_viva(relatorio)
