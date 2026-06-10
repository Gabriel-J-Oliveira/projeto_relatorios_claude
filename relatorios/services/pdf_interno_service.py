import logging
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from relatorios.models import StatusFinanceiroItem, StatusRelatorio
from relatorios.services.resumo_cliente_service import resumo_financeiro_por_cliente
from relatorios.services.snapshot_service import SnapshotError, validar_snapshot_payload


logger = logging.getLogger(__name__)


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _money(valor):
    return Decimal(str(valor or "0.00")).quantize(Decimal("0.01"))


def _date(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _datetime(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    return timezone.localtime(dt) if timezone.is_aware(dt) else dt


def _status_badge(status):
    return {
        "aprovado": "success",
        "rejeitado": "danger",
        "adjusted": "warning",
        "auto": "success",
        "approved": "success",
    }.get(status, "secondary")


def _item_status(rejeitado, solicitado, aprovado):
    if rejeitado:
        return "REJEITADO", "danger"
    if _money(solicitado) != _money(aprovado):
        return "AJUSTADO", "warning"
    return "APROVADO", "success"


def _item_rejeitado(item):
    return (
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def _texto_clientes_rateio(rateios, valor_key="valor_final"):
    partes = []
    for rateio in rateios:
        nome = rateio.get("cliente_nome") or "Cliente"
        valor = _money(rateio.get(valor_key))
        partes.append(f"{nome}: R$ {valor:.2f}".replace(".", ","))
    return "; ".join(partes) if partes else "-"


def _clientes_snapshot(payload):
    return [_ns(**cliente) for cliente in payload.get("clientes") or []]


def _tecnicos_snapshot(payload):
    return [_ns(**tecnico) for tecnico in payload.get("tecnicos") or []]


def _relatorio_snapshot(payload):
    relatorio = payload.get("relatorio") or {}
    assinatura = payload.get("assinatura_temporal") or {}
    clientes = _clientes_snapshot(payload)
    tecnicos = _tecnicos_snapshot(payload)
    return _ns(
        identificador=relatorio.get("identificador") or relatorio.get("numero") or "",
        numero=relatorio.get("numero") or "",
        status=relatorio.get("status") or "",
        status_label=relatorio.get("status_label") or "",
        tipo_relatorio=relatorio.get("tipo_relatorio") or "",
        tipo_relatorio_label=relatorio.get("tipo_relatorio_label") or "Nao informado",
        tipo_reembolso=relatorio.get("tipo_reembolso") or "reembolsavel",
        tipo_reembolso_label=relatorio.get("tipo_reembolso_label") or "Reembolsável",
        data_inicio=_date(relatorio.get("data_inicio")),
        data_fim=_date(relatorio.get("data_fim")),
        emitido_em=None,
        aprovado_em=_datetime(assinatura.get("aprovado_em") or assinatura.get("finalizado_em")),
        aprovado_por=(assinatura.get("aprovado_por") or {}).get("nome") or "",
        cidade_atendimento=relatorio.get("cidade_atendimento") or "",
        uf_atendimento=relatorio.get("uf_atendimento") or "",
        clientes=clientes,
        tecnicos=tecnicos,
        cliente_principal=clientes[0].nome if clientes else "Não informado",
        cliente_extra_count=max(len(clientes) - 1, 0),
        tecnico_principal=tecnicos[0].nome if tecnicos else "Não informado",
        tecnico_extra_count=max(len(tecnicos) - 1, 0),
    )


def _totais_snapshot(payload):
    totais = payload.get("totais") or {}
    return _ns(
        total_solicitado=_money(totais.get("total_solicitado")),
        total_aprovado=_money(totais.get("total_aprovado")),
        diferenca_removida=_money(totais.get("diferenca_removida")),
        valor_removido_reembolso=_money(
            totais.get("valor_removido_reembolso") or totais.get("diferenca_removida")
        ),
        valor_adiantamento=_money(totais.get("valor_adiantamento")),
        saldo_aprovado=_money(totais.get("total_a_reembolsar") or totais.get("saldo_aprovado")),
    )


def _distribuicao_snapshot(payload):
    linhas = []
    for item in (payload.get("distribuicao_clientes") or {}).get("clientes") or []:
        cliente = item.get("cliente") or {}
        linhas.append(
            _ns(
                cliente=cliente.get("nome") or "Não informado",
                km_total=_money(item.get("km_total")),
                despesas_solicitadas=_money(item.get("despesas_solicitadas")),
                total_solicitado=_money(item.get("total_solicitado")),
                total_aprovado=_money(item.get("total_aprovado")),
                diferenca_removida=_money(item.get("diferenca_removida")),
                motivo_viagem=item.get("motivo_viagem")
                or cliente.get("motivo_viagem")
                or "",
                status_financeiro=item.get("status_financeiro") or "Sem itens",
                tem_divergencia=bool(item.get("tem_divergencia")),
            )
        )
    return linhas


def _despesas_snapshot(payload):
    linhas = []
    for despesa in payload.get("despesas") or []:
        rateios = despesa.get("rateios") or []
        rejeitado = bool(despesa.get("rejeitado"))
        solicitado = _money(despesa.get("valor_solicitado"))
        aprovado = Decimal("0.00") if rejeitado else (
            sum((_money(rateio.get("valor_final")) for rateio in rateios), Decimal("0.00"))
            if rateios
            else _money(despesa.get("valor_final"))
        )
        status, badge = _item_status(rejeitado, solicitado, aprovado)
        linhas.append(
            _ns(
                item_id=despesa.get("id"),
                data=_date(despesa.get("data")),
                tipo=despesa.get("tipo_label") or despesa.get("tipo") or "-",
                tipo_codigo=despesa.get("tipo") or "",
                descricao=despesa.get("descricao") or "-",
                quem_pagou=despesa.get("quem_pagou") or "tecnico",
                quem_pagou_label=despesa.get("quem_pagou_label") or "",
                numero_documento=despesa.get("numero_documento_comprovante") or "-",
                valor_politica=_money(despesa.get("valor_politica"))
                if despesa.get("valor_politica") is not None
                else None,
                politica_descricao=despesa.get("politica_descricao")
                or despesa.get("politica_localidade_label")
                or "",
                excesso_politica=_money(despesa.get("excesso_politica")),
                acima_politica=bool(despesa.get("acima_politica")),
                clientes_rateio=_texto_clientes_rateio(rateios) if rateios else ", ".join(
                    cliente.get("nome") for cliente in despesa.get("clientes") or [] if cliente.get("nome")
                ) or "-",
                rateios=[
                    _ns(
                        cliente=rateio.get("cliente_nome") or "-",
                        valor_original=_money(rateio.get("valor_original")),
                        valor_final=_money(rateio.get("valor_final")),
                        status=rateio.get("status_label") or rateio.get("status") or "-",
                        motivo=rateio.get("motivo_ajuste") or "",
                    )
                    for rateio in rateios
                ],
                solicitado=solicitado,
                aprovado=_money(aprovado),
                status=status,
                badge=badge,
                rejeitado=rejeitado,
                motivo=despesa.get("motivo_rejeicao") or despesa.get("motivo_recusa") or "-",
            )
        )
    return linhas


def _trechos_snapshot(payload):
    linhas = []
    for trecho in payload.get("trechos_km") or []:
        rejeitado = bool(trecho.get("rejeitado"))
        motivo = trecho.get("motivo_rejeicao") or trecho.get("motivo_recusa") or "-"
        rateios = trecho.get("rateios") or []
        solicitado = _money(trecho.get("valor_cobranca_calculado") or trecho.get("valor_calculado"))
        aprovado = Decimal("0.00") if rejeitado else _money(trecho.get("valor_cobranca_cliente") or trecho.get("valor_final"))
        status, badge = _item_status(rejeitado, solicitado, aprovado)
        linhas.append(
            _ns(
                item_id=trecho.get("id"),
                data=_date(trecho.get("data")),
                origem=trecho.get("origem") or "-",
                destino=trecho.get("destino") or "-",
                km=_money(trecho.get("km")),
                km_calculado_api=_money(trecho.get("km_calculado_api")),
                solicitado=solicitado,
                valor_reembolso_tecnico=_money(trecho.get("valor_reembolso_tecnico")),
                excesso_reducao=_money(trecho.get("diferenca") or trecho.get("excesso_reducao")),
                total_final=_money(aprovado),
                status=status,
                badge=badge,
                rejeitado=rejeitado,
                motivo=motivo,
                rateios=[
                    _ns(
                        cliente=rateio.get("cliente_nome") or "-",
                        km=_money(rateio.get("km_cliente") or rateio.get("km_final")),
                        valor_km=Decimal(str(rateio.get("valor_km_cliente_contratual") or rateio.get("valor_km") or "0.00")),
                        valor_original=_money(rateio.get("valor_cobranca_calculado") or rateio.get("valor_calculado")),
                        valor_final=_money(rateio.get("valor_cobranca_cliente") or rateio.get("valor_final")),
                        status=rateio.get("status_label") or rateio.get("status") or "-",
                        motivo=rateio.get("motivo_ajuste") or "",
                    )
                    for rateio in rateios
                ],
            )
        )
    return linhas


def _justificativas_snapshot(payload):
    justificativas = []
    for obs in payload.get("observacoes") or []:
        texto = obs.get("texto")
        if texto:
            justificativas.append(_ns(titulo=obs.get("titulo") or "Observação", texto=texto))
    for despesa in payload.get("despesas") or []:
        for rateio in despesa.get("rateios") or []:
            if rateio.get("motivo_ajuste"):
                justificativas.append(
                    _ns(
                        titulo=f"Rateio despesa: {despesa.get('descricao') or despesa.get('id')}",
                        texto=rateio.get("motivo_ajuste"),
                    )
                )
    for trecho in payload.get("trechos_km") or []:
        for rateio in trecho.get("rateios") or []:
            if rateio.get("motivo_ajuste"):
                justificativas.append(
                    _ns(
                        titulo=f"Rateio KM: {trecho.get('descricao') or trecho.get('id')}",
                        texto=rateio.get("motivo_ajuste"),
                    )
                )
    return justificativas


def _historico_snapshot(payload):
    historicos = []
    for historico in (payload.get("historico") or [])[:12]:
        usuario = historico.get("usuario") or {}
        historicos.append(
            _ns(
                data_hora=_datetime(historico.get("data_hora")),
                usuario=usuario.get("nome") or usuario.get("username") or "Sistema",
                acao=historico.get("acao") or historico.get("tipo_evento_label") or "-",
            )
        )
    return historicos


def _anexos_snapshot(payload):
    return [
        _ns(
            data=None,
            descricao=anexo.get("descricao") or "-",
            arquivo=anexo.get("nome") or anexo.get("path") or "-",
        )
        for anexo in payload.get("anexos") or []
    ]


def _avisos_snapshot(payload):
    avisos = []
    distribuicao = payload.get("distribuicao_clientes") or {}
    avisos.extend(distribuicao.get("erros") or [])
    for cliente in distribuicao.get("clientes") or []:
        if cliente.get("tem_divergencia"):
            nome = (cliente.get("cliente") or {}).get("nome") or "Cliente"
            avisos.append(f"{nome}: possui diferença financeira ou itens rejeitados.")
    for despesa in payload.get("despesas") or []:
        if not despesa.get("comprovante"):
            avisos.append(f"Despesa sem comprovante: {despesa.get('descricao') or despesa.get('id')}.")
        if despesa.get("rejeitado"):
            avisos.append(f"Despesa rejeitada: {despesa.get('descricao') or despesa.get('id')}.")
        if despesa.get("acima_politica"):
            avisos.append(
                f"Despesa acima da politica: {despesa.get('descricao') or despesa.get('id')}."
            )
        if any(rateio.get("status") == "adjusted" for rateio in despesa.get("rateios") or []):
            avisos.append(f"Rateio ajustado em despesa: {despesa.get('descricao') or despesa.get('id')}.")
    for trecho in payload.get("trechos_km") or []:
        if trecho.get("rejeitado"):
            avisos.append(f"Trecho KM rejeitado: {trecho.get('descricao') or trecho.get('id')}.")
        if any(rateio.get("status") == "adjusted" for rateio in trecho.get("rateios") or []):
            avisos.append(f"Rateio/valor KM ajustado: {trecho.get('descricao') or trecho.get('id')}.")
    return list(dict.fromkeys(avisos))


def _payload_snapshot(relatorio):
    try:
        snapshot = relatorio.snapshot_financeiro
    except ObjectDoesNotExist:
        return None
    try:
        validar_snapshot_payload(snapshot.payload or {})
    except SnapshotError as exc:
        logger.error(
            "Snapshot financeiro invalido no PDF interno do relatorio %s. Usando fallback legado. Erros: %s",
            relatorio.pk,
            exc,
        )
        return None
    return snapshot.payload or {}


def _relatorio_vivo(relatorio):
    clientes = [_ns(nome=cliente.nome) for cliente in relatorio.clientes_exibicao()]
    tecnicos = [_ns(nome=tecnico.nome) for tecnico in relatorio.tecnicos_exibicao()]
    return _ns(
        identificador=relatorio.identificador,
        numero=relatorio.numero or relatorio.identificador,
        status=relatorio.status,
        status_label=relatorio.get_status_display(),
        tipo_relatorio=relatorio.tipo_relatorio,
        tipo_relatorio_label=relatorio.get_tipo_relatorio_display(),
        tipo_reembolso=relatorio.tipo_reembolso,
        tipo_reembolso_label=relatorio.get_tipo_reembolso_display(),
        data_inicio=relatorio.data_inicio,
        data_fim=relatorio.data_fim,
        aprovado_em=relatorio.aprovado_em,
        aprovado_por=(relatorio.aprovado_por.get_full_name() or relatorio.aprovado_por.username) if relatorio.aprovado_por else "",
        cidade_atendimento=relatorio.cidade_atendimento,
        uf_atendimento=relatorio.uf_atendimento,
        clientes=clientes,
        tecnicos=tecnicos,
        cliente_principal=clientes[0].nome if clientes else "Não informado",
        cliente_extra_count=max(len(clientes) - 1, 0),
        tecnico_principal=tecnicos[0].nome if tecnicos else "Não informado",
        tecnico_extra_count=max(len(tecnicos) - 1, 0),
    )


def _totais_vivos(relatorio):
    return _ns(
        total_solicitado=relatorio.total_solicitado,
        total_aprovado=relatorio.total_aprovado,
        diferenca_removida=relatorio.diferenca_removida,
        valor_removido_reembolso=relatorio.valor_removido_reembolso,
        valor_adiantamento=relatorio.valor_adiantamento,
        saldo_aprovado=relatorio.total_a_reembolsar,
    )


def _distribuicao_viva(relatorio):
    return [
        _ns(
            cliente=resumo.cliente.nome,
            km_total=resumo.km_total,
            despesas_solicitadas=resumo.despesas_solicitadas,
            total_solicitado=resumo.total_solicitado,
            total_aprovado=resumo.total_aprovado,
            diferenca_removida=resumo.diferenca_removida,
            motivo_viagem=getattr(resumo, "motivo_viagem", "") or "",
            status_financeiro=resumo.status_financeiro,
            tem_divergencia=resumo.tem_divergencia,
        )
        for resumo in resumo_financeiro_por_cliente(relatorio)["clientes"]
    ]


def _despesas_vivas(relatorio):
    linhas = []
    for despesa in relatorio.despesas.all():
        rateios = list(despesa.rateios.all())
        rejeitado = _item_rejeitado(despesa)
        aprovado = Decimal("0.00") if rejeitado else (
            sum((rateio.valor_final for rateio in rateios), Decimal("0.00"))
            if rateios
            else despesa.valor_final
        )
        status, badge = _item_status(rejeitado, despesa.valor, aprovado)
        linhas.append(
            _ns(
                item_id=despesa.pk,
                data=despesa.data,
                tipo=despesa.get_tipo_display(),
                tipo_codigo=despesa.tipo,
                descricao=despesa.descricao,
                quem_pagou=despesa.quem_pagou,
                quem_pagou_label=despesa.get_quem_pagou_display(),
                numero_documento=despesa.numero_documento_comprovante or "-",
                valor_politica=_money(despesa.valor_politica)
                if despesa.valor_politica is not None
                else None,
                politica_descricao=despesa.politica_localidade_label,
                excesso_politica=_money(despesa.excesso_politica),
                acima_politica=bool(despesa.acima_politica),
                clientes_rateio="; ".join(
                    f"{rateio.cliente.nome}: R$ {rateio.valor_final:.2f}".replace(".", ",")
                    for rateio in rateios
                ) or ", ".join(v.cliente.nome for v in despesa.clientes_vinculados.all()) or "-",
                rateios=[
                    _ns(
                        cliente=rateio.cliente.nome,
                        valor_original=_money(rateio.valor_original),
                        valor_final=_money(rateio.valor_final),
                        status=rateio.get_status_display(),
                        motivo=rateio.motivo_ajuste or "",
                    )
                    for rateio in rateios
                ],
                solicitado=despesa.valor,
                aprovado=_money(aprovado),
                status=status,
                badge=badge,
                rejeitado=rejeitado,
                motivo=despesa.motivo_rejeicao or despesa.motivo_recusa or "-",
            )
        )
    return linhas


def _trechos_vivos(relatorio):
    linhas = []
    for trecho in relatorio.trechos.all():
        rejeitado = _item_rejeitado(trecho)
        motivo = trecho.motivo_rejeicao or trecho.motivo_recusa or "-"
        rateios = list(trecho.rateios.all())
        solicitado = _money(trecho.valor_calculado_clientes)
        aprovado = Decimal("0.00") if rejeitado else _money(trecho.valor_final_clientes)
        status, badge = _item_status(rejeitado, solicitado, aprovado)
        linhas.append(
            _ns(
                item_id=trecho.pk,
                data=trecho.data,
                origem=trecho.origem,
                destino=trecho.destino,
                km=trecho.km,
                km_calculado_api=trecho.km_calculado_api or Decimal("0.00"),
                solicitado=solicitado,
                valor_reembolso_tecnico=_money(trecho.valor_reembolso_tecnico),
                excesso_reducao=_money(trecho.excesso_reducao_km),
                total_final=_money(aprovado),
                status=status,
                badge=badge,
                rejeitado=rejeitado,
                motivo=motivo,
                rateios=[
                    _ns(
                        cliente=rateio.cliente.nome,
                        km=_money(rateio.km_cliente),
                        valor_km=rateio.valor_km,
                        valor_original=_money(rateio.valor_calculado),
                        valor_final=_money(rateio.valor_final),
                        status=rateio.get_status_display(),
                        motivo=rateio.motivo_ajuste or "",
                    )
                    for rateio in rateios
                ],
            )
        )
    return linhas


def _justificativas_vivas(relatorio):
    justificativas = []
    if relatorio.motivo_rejeicao:
        justificativas.append(_ns(titulo="Justificativa do relatório", texto=relatorio.motivo_rejeicao))
    for despesa in relatorio.despesas.all():
        motivo = despesa.motivo_rejeicao or despesa.motivo_recusa
        if motivo:
            justificativas.append(_ns(titulo=f"Despesa: {despesa.descricao}", texto=motivo))
        for rateio in despesa.rateios.all():
            if rateio.motivo_ajuste:
                justificativas.append(_ns(titulo=f"Rateio despesa: {despesa.descricao}", texto=rateio.motivo_ajuste))
    for trecho in relatorio.trechos.all():
        motivo = trecho.motivo_rejeicao or trecho.motivo_recusa
        if motivo:
            justificativas.append(_ns(titulo=f"Trecho KM: {trecho.origem} -> {trecho.destino}", texto=motivo))
        for rateio in trecho.rateios.all():
            if rateio.motivo_ajuste:
                justificativas.append(_ns(titulo=f"Rateio KM: {trecho.origem} -> {trecho.destino}", texto=rateio.motivo_ajuste))
    return justificativas


def _categoria_contabil(tipo):
    mapa = {
        "alimentacao": "Alimentacao",
        "hospedagem": "Hotel",
        "transporte": "Taxi/Uber/Passagem",
        "pedagio": "Pedagio",
        "combustivel": "Veiculos",
        "estacionamento": "Veiculos",
    }
    return mapa.get(tipo, "Outros")


def _aplicar_visao_contabil(pdf):
    despesas = list(pdf.despesas or [])
    pdf.despesas_tecnico = [
        despesa for despesa in despesas if getattr(despesa, "quem_pagou", "tecnico") == "tecnico"
    ]
    pdf.despesas_empresa = [
        despesa for despesa in despesas if getattr(despesa, "quem_pagou", "tecnico") == "empresa"
    ]

    categorias = {}

    def linha_categoria(nome):
        if nome not in categorias:
            categorias[nome] = {"categoria": nome, "tecnico": Decimal("0.00"), "empresa": Decimal("0.00")}
        return categorias[nome]

    for despesa in despesas:
        linha = linha_categoria(_categoria_contabil(getattr(despesa, "tipo_codigo", "") or ""))
        destino = "empresa" if getattr(despesa, "quem_pagou", "tecnico") == "empresa" else "tecnico"
        linha[destino] += _money(getattr(despesa, "aprovado", Decimal("0.00")))

    trechos_aprovados = [t for t in pdf.trechos if not getattr(t, "rejeitado", False)]
    km_total_reembolso = sum(
        (_money(getattr(t, "km", 0)) for t in trechos_aprovados),
        Decimal("0.00"),
    )
    km_tecnico = sum(
        (_money(getattr(t, "valor_reembolso_tecnico", 0)) for t in trechos_aprovados),
        Decimal("0.00"),
    )
    km_cliente = sum(
        (_money(getattr(t, "total_final", 0)) for t in trechos_aprovados),
        Decimal("0.00"),
    )
    if km_tecnico or km_cliente:
        linha = linha_categoria("Quilometragem")
        linha["tecnico"] += km_tecnico
        linha["empresa"] += Decimal("0.00")
        pdf.total_km_cobrar_cliente = km_cliente
    else:
        pdf.total_km_cobrar_cliente = Decimal("0.00")

    pdf.total_pago_tecnico = sum((_money(d.aprovado) for d in pdf.despesas_tecnico), Decimal("0.00"))
    pdf.total_pago_empresa = sum((_money(d.aprovado) for d in pdf.despesas_empresa), Decimal("0.00"))
    pdf.km_total_reembolso = _money(km_total_reembolso)
    pdf.total_km_reembolso_tecnico = _money(km_tecnico)
    pdf.valor_km_reembolso_tecnico_unitario = _money(
        km_tecnico / km_total_reembolso if km_total_reembolso > 0 else Decimal("1.35")
    )
    pdf.total_reembolso_tecnico = _money(pdf.totais.saldo_aprovado)
    pdf.lancamentos_contabeis = [
        _ns(
            categoria=item["categoria"],
            tecnico=_money(item["tecnico"]),
            empresa=_money(item["empresa"]),
            total=_money(item["tecnico"] + item["empresa"]),
        )
        for item in categorias.values()
        if item["tecnico"] or item["empresa"]
    ]
    return pdf


def montar_contexto_pdf_interno(relatorio, emitido_em, usuario_gerador=None, avisos_financeiro=None):
    payload = _payload_snapshot(relatorio)
    if payload:
        pdf = _ns(
            usa_snapshot=True,
            relatorio=_relatorio_snapshot(payload),
            totais=_totais_snapshot(payload),
            distribuicao_clientes=_distribuicao_snapshot(payload),
            despesas=_despesas_snapshot(payload),
            trechos=_trechos_snapshot(payload),
            avisos=_avisos_snapshot(payload),
            justificativas=_justificativas_snapshot(payload),
            historicos=_historico_snapshot(payload),
            anexos=_anexos_snapshot(payload),
        )
    else:
        if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
            logger.warning(
                "PDF interno do relatorio finalizado %s gerado com dados vivos por ausencia de snapshot.",
                relatorio.pk,
            )
        pdf = _ns(
            usa_snapshot=False,
            relatorio=_relatorio_vivo(relatorio),
            totais=_totais_vivos(relatorio),
            distribuicao_clientes=_distribuicao_viva(relatorio),
            despesas=_despesas_vivas(relatorio),
            trechos=_trechos_vivos(relatorio),
            avisos=[aviso.get("mensagem") for aviso in (avisos_financeiro or [])],
            justificativas=_justificativas_vivas(relatorio),
            historicos=[
                _ns(
                    data_hora=historico.data_hora,
                    usuario=(historico.usuario.get_full_name() or historico.usuario.username) if historico.usuario else "Sistema",
                    acao=historico.acao,
                )
                for historico in relatorio.historicos.all()[:12]
            ],
            anexos=[
                _ns(data=despesa.data, descricao=despesa.descricao, arquivo=despesa.comprovante.name)
                for despesa in relatorio.despesas.all()
                if despesa.comprovante
            ],
        )

    pdf.emitido_em = emitido_em
    pdf.usuario_gerador = usuario_gerador
    return _aplicar_visao_contabil(pdf)
