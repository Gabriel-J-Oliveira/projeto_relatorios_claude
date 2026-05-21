import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from relatorios.models import (
    RelatorioSnapshotFinanceiro,
    StatusFinanceiroItem,
    StatusRelatorio,
)
from relatorios.services.resumo_cliente_service import resumo_financeiro_por_cliente


SCHEMA_VERSION = 1
ESTADOS_COM_SNAPSHOT = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


class SnapshotError(Exception):
    pass


def _money(valor):
    return (valor or Decimal("0.00")).quantize(Decimal("0.01"))


def _decimal(valor):
    if valor is None:
        return None
    return str(valor)


def _date(valor):
    return valor.isoformat() if valor else None


def _datetime(valor):
    return valor.isoformat() if valor else None


def _user_payload(usuario):
    if not usuario:
        return None
    return {
        "id": usuario.pk,
        "username": usuario.get_username(),
        "nome": usuario.get_full_name() or usuario.get_username(),
        "email": getattr(usuario, "email", "") or "",
    }


def _arquivo_payload(arquivo):
    if not arquivo:
        return None
    try:
        url = arquivo.url
    except ValueError:
        url = ""
    return {
        "nome": arquivo.name.rsplit("/", 1)[-1],
        "path": arquivo.name,
        "url": url,
    }


def _cliente_payload(cliente, ordem=0):
    return {
        "id": cliente.pk,
        "nome": cliente.nome,
        "documento": cliente.cnpj_cpf or "",
        "cidade": cliente.cidade or "",
        "uf": cliente.uf or "",
        "cidade_uf": cliente.cidade_uf,
        "valor_km": _decimal(cliente.valor_km),
        "ordem": ordem,
    }


def _tecnico_payload(tecnico, papel=""):
    return {
        "id": tecnico.pk,
        "nome": tecnico.nome,
        "email": tecnico.email or "",
        "telefone": tecnico.telefone or "",
        "papel": papel,
    }


def _status_rejeitado(item):
    return (
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def _rateio_despesa_payload(rateio):
    return {
        "id": rateio.pk,
        "cliente_id": rateio.cliente_id,
        "cliente_nome": rateio.cliente.nome,
        "valor_original": _decimal(rateio.valor_original),
        "valor_final": _decimal(rateio.valor_final),
        "percentual": _decimal(rateio.percentual),
        "status": rateio.status,
        "status_label": rateio.get_status_display(),
        "motivo_ajuste": rateio.motivo_ajuste,
        "alterado_por": _user_payload(rateio.alterado_por),
        "created_at": _datetime(rateio.created_at),
        "updated_at": _datetime(rateio.updated_at),
    }


def _rateio_km_payload(rateio):
    return {
        "id": rateio.pk,
        "cliente_id": rateio.cliente_id,
        "cliente_nome": rateio.cliente.nome,
        "km_original": _decimal(rateio.km_original),
        "km_final": _decimal(rateio.km_final),
        "km_cliente": _decimal(rateio.km_cliente),
        "valor_km": _decimal(rateio.valor_km),
        "valor_calculado": _decimal(rateio.valor_calculado),
        "valor_rateado": _decimal(rateio.valor_rateado),
        "valor_final": _decimal(rateio.valor_final),
        "status": rateio.status,
        "status_label": rateio.get_status_display(),
        "motivo_ajuste": rateio.motivo_ajuste,
        "alterado_por": _user_payload(rateio.alterado_por),
        "created_at": _datetime(rateio.created_at),
        "updated_at": _datetime(rateio.updated_at),
    }


def _despesa_payload(despesa):
    return {
        "id": despesa.pk,
        "ordem": despesa.ordem,
        "data": _date(despesa.data),
        "tipo": despesa.tipo,
        "tipo_label": despesa.get_tipo_display(),
        "descricao": despesa.descricao,
        "valor_solicitado": _decimal(despesa.valor),
        "valor_aprovado": _decimal(despesa.valor_aprovado),
        "valor_final": _decimal(despesa.valor_final),
        "quem_pagou": despesa.quem_pagou,
        "quem_pagou_label": despesa.get_quem_pagou_display(),
        "status_financeiro": despesa.status_financeiro,
        "status_financeiro_label": despesa.get_status_financeiro_display(),
        "rejeitado": _status_rejeitado(despesa),
        "motivo_recusa": despesa.motivo_recusa,
        "motivo_rejeicao": despesa.motivo_rejeicao,
        "rejeitado_por": _user_payload(despesa.rejeitado_por),
        "rejeitado_em": _datetime(despesa.rejeitado_em),
        "observacoes": despesa.observacoes,
        "comprovante": _arquivo_payload(despesa.comprovante),
        "clientes": [
            _cliente_payload(vinculo.cliente)
            for vinculo in despesa.clientes_vinculados.all()
        ],
        "rateios": [_rateio_despesa_payload(rateio) for rateio in despesa.rateios.all()],
    }


def _trecho_payload(trecho):
    return {
        "id": trecho.pk,
        "ordem": trecho.ordem,
        "data": _date(trecho.data),
        "origem": trecho.origem,
        "destino": trecho.destino,
        "descricao": f"{trecho.origem} -> {trecho.destino}",
        "km": _decimal(trecho.km),
        "valor_km": _decimal(trecho.valor_km),
        "valor_km_aprovado": _decimal(trecho.valor_km_aprovado),
        "valor_km_final": _decimal(trecho.valor_km_final),
        "valor_calculado": _decimal(trecho.valor_calculado_clientes),
        "valor_final": _decimal(trecho.valor_final_clientes),
        "status_financeiro": trecho.status_financeiro,
        "status_financeiro_label": trecho.get_status_financeiro_display(),
        "rejeitado": _status_rejeitado(trecho),
        "motivo_recusa": trecho.motivo_recusa,
        "motivo_rejeicao": trecho.motivo_rejeicao,
        "rejeitado_por": _user_payload(trecho.rejeitado_por),
        "rejeitado_em": _datetime(trecho.rejeitado_em),
        "observacao": trecho.observacao,
        "comprovante": _arquivo_payload(getattr(trecho, "comprovante", None)),
        "clientes": [
            _cliente_payload(vinculo.cliente)
            for vinculo in trecho.clientes_vinculados.all()
        ],
        "rateios": [_rateio_km_payload(rateio) for rateio in trecho.rateios.all()],
    }


def _distribuicao_payload(relatorio):
    distribuicao = resumo_financeiro_por_cliente(relatorio)
    clientes = []
    for resumo in distribuicao["clientes"]:
        clientes.append(
            {
                "cliente": _cliente_payload(resumo.cliente),
                "km_total": _decimal(resumo.km_total),
                "valor_km_solicitado": _decimal(resumo.valor_km_solicitado),
                "despesas_solicitadas": _decimal(resumo.despesas_solicitadas),
                "total_solicitado": _decimal(resumo.total_solicitado),
                "total_aprovado": _decimal(resumo.total_aprovado),
                "diferenca_removida": _decimal(resumo.diferenca_removida),
                "itens_rejeitados": resumo.itens_rejeitados,
                "status_financeiro": resumo.status_financeiro,
                "tem_divergencia": resumo.tem_divergencia,
            }
        )
    return {
        "clientes": clientes,
        "total": distribuicao["total"],
        "erros": list(distribuicao.get("erros") or []),
    }


def _historico_payload(relatorio):
    return [
        {
            "id": historico.pk,
            "tipo_evento": historico.tipo_evento,
            "tipo_evento_label": historico.get_tipo_evento_display(),
            "acao": historico.acao,
            "descricao": historico.descricao,
            "data_hora": _datetime(historico.data_hora),
            "usuario": _user_payload(historico.usuario),
            "dados_json": historico.dados_json or {},
        }
        for historico in relatorio.historicos.all()
    ]


def construir_snapshot_financeiro(relatorio, usuario=None):
    finalizado_em = timezone.now()
    clientes = [
        _cliente_payload(cliente, ordem=idx)
        for idx, cliente in enumerate(relatorio.clientes_exibicao())
    ]
    tecnicos = []
    for idx, tecnico in enumerate(relatorio.tecnicos_exibicao()):
        papel = "Responsavel" if idx == 0 else "Apoio"
        tecnicos.append(_tecnico_payload(tecnico, papel=papel))

    despesas = [_despesa_payload(despesa) for despesa in relatorio.despesas.all()]
    trechos = [_trecho_payload(trecho) for trecho in relatorio.trechos.all()]
    anexos = []
    for despesa in despesas:
        if despesa["comprovante"]:
            anexos.append(
                {
                    "tipo": "Comprovante",
                    "descricao": despesa["descricao"],
                    **despesa["comprovante"],
                }
            )
    for trecho in trechos:
        if trecho["comprovante"]:
            anexos.append(
                {
                    "tipo": "Comprovante KM",
                    "descricao": trecho["descricao"],
                    **trecho["comprovante"],
                }
            )

    observacoes = []
    if relatorio.observacoes:
        observacoes.append({"titulo": "Observacoes gerais", "texto": relatorio.observacoes})
    if relatorio.motivo_rejeicao:
        observacoes.append(
            {"titulo": "Justificativa financeira", "texto": relatorio.motivo_rejeicao}
        )
    for despesa in despesas:
        motivo = despesa["motivo_rejeicao"] or despesa["motivo_recusa"]
        if motivo:
            observacoes.append({"titulo": f"Despesa {despesa['id']}", "texto": motivo})
    for trecho in trechos:
        motivo = trecho["motivo_rejeicao"] or trecho["motivo_recusa"]
        if motivo:
            observacoes.append({"titulo": f"Trecho KM {trecho['id']}", "texto": motivo})

    return {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": _datetime(finalizado_em),
        "gerado_por": _user_payload(usuario),
        "relatorio": {
            "id": relatorio.pk,
            "numero": relatorio.numero,
            "identificador": relatorio.identificador,
            "status": relatorio.status,
            "status_label": relatorio.get_status_display(),
            "tipo_relatorio": relatorio.tipo_relatorio,
            "tipo_relatorio_label": relatorio.get_tipo_relatorio_display(),
            "centro_custo": relatorio.centro_custo,
            "cidade_atendimento": relatorio.cidade_atendimento,
            "uf_atendimento": relatorio.uf_atendimento,
            "tipo_localidade": relatorio.tipo_localidade,
            "tipo_localidade_label": relatorio.get_tipo_localidade_display(),
            "data_inicio": _date(relatorio.data_inicio),
            "data_fim": _date(relatorio.data_fim),
            "motivo": relatorio.motivo,
            "observacoes": relatorio.observacoes,
            "motivo_rejeicao": relatorio.motivo_rejeicao,
            "criado_em": _datetime(relatorio.criado_em),
            "atualizado_em": _datetime(relatorio.atualizado_em),
        },
        "assinatura_temporal": {
            "finalizado_em": _datetime(finalizado_em),
            "finalizado_por": _user_payload(usuario),
            "aprovado_em": _datetime(relatorio.aprovado_em),
            "aprovado_por": _user_payload(relatorio.aprovado_por),
        },
        "clientes": clientes,
        "tecnicos": tecnicos,
        "despesas": despesas,
        "trechos_km": trechos,
        "distribuicao_clientes": _distribuicao_payload(relatorio),
        "totais": {
            "total_despesas_tecnico": _decimal(relatorio.total_despesas_tecnico),
            "total_despesas_empresa": _decimal(relatorio.total_despesas_empresa),
            "total_despesas": _decimal(relatorio.total_despesas),
            "total_km": _decimal(relatorio.total_km),
            "total_km_percorrido": _decimal(relatorio.total_km_percorrido),
            "total_solicitado": _decimal(relatorio.total_solicitado),
            "total_aprovado_despesas": _decimal(relatorio.total_aprovado_despesas),
            "total_aprovado_km": _decimal(relatorio.total_aprovado_km),
            "total_aprovado": _decimal(relatorio.total_aprovado),
            "diferenca_removida": _decimal(relatorio.diferenca_removida),
            "valor_adiantamento": _decimal(relatorio.valor_adiantamento),
            "saldo": _decimal(relatorio.saldo),
            "saldo_aprovado": _decimal(relatorio.saldo_aprovado),
        },
        "contagens": {
            "despesas": len(despesas),
            "trechos_km": len(trechos),
            "itens_rejeitados": sum(1 for item in despesas + trechos if item["rejeitado"]),
        },
        "observacoes": observacoes,
        "anexos": anexos,
        "historico": _historico_payload(relatorio),
    }


def calcular_checksum(payload):
    serializado = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def validar_snapshot_payload(payload):
    erros = []
    if not payload.get("clientes"):
        erros.append("Snapshot financeiro sem clientes.")
    if not payload.get("tecnicos"):
        erros.append("Snapshot financeiro sem tecnicos.")
    totais = payload.get("totais") or {}
    total_aprovado = Decimal(str(totais.get("total_aprovado") or "0.00"))
    total_solicitado = Decimal(str(totais.get("total_solicitado") or "0.00"))
    soma_solicitada = Decimal("0.00")
    soma_aprovada = Decimal("0.00")

    for despesa in payload.get("despesas") or []:
        soma_solicitada += _money(Decimal(str(despesa.get("valor_solicitado") or "0.00")))
        rateios = despesa.get("rateios") or []
        if rateios:
            soma_aprovada += sum(
                (_money(Decimal(str(rateio.get("valor_final") or "0.00"))) for rateio in rateios),
                Decimal("0.00"),
            )
        else:
            soma_aprovada += _money(Decimal(str(despesa.get("valor_final") or "0.00")))

    for trecho in payload.get("trechos_km") or []:
        soma_solicitada += _money(Decimal(str(trecho.get("valor_calculado") or "0.00")))
        rateios = trecho.get("rateios") or []
        if rateios:
            soma_aprovada += sum(
                (_money(Decimal(str(rateio.get("valor_final") or "0.00"))) for rateio in rateios),
                Decimal("0.00"),
            )
        else:
            soma_aprovada += _money(Decimal(str(trecho.get("valor_final") or "0.00")))

    if _money(soma_solicitada) != _money(total_solicitado):
        erros.append("Snapshot financeiro nao fecha com o total solicitado.")
    if _money(soma_aprovada) != _money(total_aprovado):
        erros.append("Snapshot financeiro nao fecha com o total aprovado.")
    if payload.get("relatorio", {}).get("status") == StatusRelatorio.APROVADO and total_aprovado <= 0:
        erros.append("Snapshot de relatorio aprovado com total aprovado zerado.")
    if erros:
        raise SnapshotError(erros)


@transaction.atomic
def criar_snapshot_financeiro(relatorio, usuario=None):
    if relatorio.status not in ESTADOS_COM_SNAPSHOT:
        raise SnapshotError("Snapshot financeiro so pode ser criado para relatorio finalizado.")

    try:
        return relatorio.snapshot_financeiro
    except ObjectDoesNotExist:
        pass

    payload = construir_snapshot_financeiro(relatorio, usuario)
    validar_snapshot_payload(payload)
    checksum = calcular_checksum(payload)
    finalizado_em = timezone.now()
    return RelatorioSnapshotFinanceiro.objects.create(
        relatorio=relatorio,
        schema_version=SCHEMA_VERSION,
        numero=relatorio.numero or relatorio.identificador,
        status=relatorio.status,
        total_solicitado=_money(relatorio.total_solicitado),
        total_aprovado=_money(relatorio.total_aprovado),
        diferenca_removida=_money(relatorio.diferenca_removida),
        payload=payload,
        checksum=checksum,
        finalizado_em=finalizado_em,
        finalizado_por=usuario if getattr(usuario, "is_authenticated", False) else None,
    )
