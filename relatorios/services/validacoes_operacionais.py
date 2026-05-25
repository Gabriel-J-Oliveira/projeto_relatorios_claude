from dataclasses import dataclass, field
from decimal import Decimal

from relatorios.models import StatusFinanceiroItem, StatusRelatorio
from relatorios.services.financeiro_validator import (
    validar_integridade_financeira_relatorio,
)


ESTADOS_FINAIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


@dataclass(frozen=True)
class ResultadoValidacaoOperacional:
    ok: bool = True
    errors: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def sucesso(cls):
        return cls(ok=True, errors=())

    @classmethod
    def falha(cls, erros):
        erros = tuple(str(erro) for erro in erros if str(erro).strip())
        return cls(ok=not erros, errors=erros)

    @property
    def primeira_mensagem(self):
        return self.errors[0] if self.errors else ""


def calcular_total_aprovado(relatorio):
    return relatorio.total_aprovado


def _item_despesa_ativo(despesa):
    return not (
        despesa.rejeitado
        or despesa.status_financeiro == StatusFinanceiroItem.REJEITADO
    )


def _trecho_ativo(trecho):
    return not (
        trecho.rejeitado
        or trecho.status_financeiro == StatusFinanceiroItem.REJEITADO
    )


def relatorio_tem_itens_validos(relatorio):
    return (
        relatorio.despesas.exists()
        or relatorio.trechos.exists()
        or (relatorio.km_excedente_interno or Decimal("0.00")) > 0
    )


def relatorio_tem_itens_ativos(relatorio):
    despesas_ativas = relatorio.despesas.filter(
        rejeitado=False,
    ).exclude(status_financeiro=StatusFinanceiroItem.REJEITADO)
    trechos_ativos = relatorio.trechos.filter(
        rejeitado=False,
    ).exclude(status_financeiro=StatusFinanceiroItem.REJEITADO)
    return (
        despesas_ativas.exists()
        or trechos_ativos.exists()
        or (relatorio.km_excedente_interno or Decimal("0.00")) > 0
    )


def validar_motivos_clientes_relatorio(relatorio):
    vinculos = list(relatorio.clientes_vinculados.select_related("cliente").all())
    if not vinculos and relatorio.cliente_id:
        return [] if (relatorio.motivo or "").strip() else [
            "Informe o motivo da viagem para todos os clientes do relatorio."
        ]
    sem_motivo = [
        vinculo.cliente.nome
        for vinculo in vinculos
        if not (vinculo.motivo_viagem or "").strip()
    ]
    if sem_motivo:
        return ["Informe o motivo da viagem para todos os clientes do relatorio."]
    return []


def validar_valores_negativos(relatorio):
    erros = []

    for despesa in relatorio.despesas.all():
        if despesa.valor is not None and despesa.valor < 0:
            erros.append(f"Despesa {despesa.pk} possui valor solicitado negativo.")
        if despesa.valor_aprovado is not None and despesa.valor_aprovado < 0:
            erros.append(f"Despesa {despesa.pk} possui valor aprovado negativo.")

    for trecho in relatorio.trechos.all():
        if trecho.km is not None and trecho.km < 0:
            erros.append(f"Trecho KM {trecho.pk} possui quilometragem negativa.")
        if trecho.valor_km is not None and trecho.valor_km < 0:
            erros.append(f"Trecho KM {trecho.pk} possui valor por KM negativo.")
        if trecho.valor_km_aprovado is not None and trecho.valor_km_aprovado < 0:
            erros.append(f"Trecho KM {trecho.pk} possui valor por KM aprovado negativo.")

    if relatorio.km_excedente_interno is not None and relatorio.km_excedente_interno < 0:
        erros.append("KM excedente / deslocamento interno nao pode ser negativo.")

    return erros


def validar_relatorio_para_edicao(relatorio):
    if relatorio.status in ESTADOS_FINAIS:
        return ResultadoValidacaoOperacional.falha(
            ["Relatorio aprovado ou rejeitado esta bloqueado para alteracoes."]
        )
    return ResultadoValidacaoOperacional.sucesso()


def validar_relatorio_para_envio(relatorio):
    erros = []

    if relatorio.status not in {StatusRelatorio.RASCUNHO, StatusRelatorio.AJUSTE}:
        erros.append("Este relatorio nao pode ser enviado no status atual.")

    if relatorio.status in ESTADOS_FINAIS:
        erros.append("Relatorio finalizado nao pode ser reenviado.")

    if not relatorio_tem_itens_validos(relatorio):
        erros.append("Adicione pelo menos uma despesa ou trecho de KM antes de enviar.")

    erros.extend(validar_valores_negativos(relatorio))
    erros.extend(validar_motivos_clientes_relatorio(relatorio))
    erros.extend(validar_integridade_financeira_relatorio(relatorio))

    return ResultadoValidacaoOperacional.falha(erros)


def validar_relatorio_para_aprovacao(relatorio):
    erros = []

    if relatorio.status != StatusRelatorio.CONFERENCIA:
        erros.append("Somente relatorios em conferencia pendente podem ser aprovados.")

    if relatorio.status in ESTADOS_FINAIS:
        erros.append("Relatorio finalizado nao pode sofrer nova aprovacao.")

    if not relatorio_tem_itens_validos(relatorio):
        erros.append("O relatorio nao possui despesas ou trechos de KM.")

    if not relatorio_tem_itens_ativos(relatorio):
        erros.append("Todos os itens do relatorio estao rejeitados ou inativos.")

    total_aprovado = calcular_total_aprovado(relatorio)
    if total_aprovado <= Decimal("0.00"):
        erros.append("O valor aprovado total e R$ 0,00.")

    erros.extend(validar_valores_negativos(relatorio))
    erros.extend(validar_motivos_clientes_relatorio(relatorio))
    erros.extend(validar_integridade_financeira_relatorio(relatorio))

    return ResultadoValidacaoOperacional.falha(erros)


def validar_transicao_status(relatorio, novo_status):
    erros = []

    if relatorio.status in ESTADOS_FINAIS:
        erros.append("Relatorio aprovado ou rejeitado esta bloqueado para alteracoes.")

    transicoes = {
        StatusRelatorio.RASCUNHO: {StatusRelatorio.CONFERENCIA},
        StatusRelatorio.CONFERENCIA: {
            StatusRelatorio.AJUSTE,
            StatusRelatorio.APROVADO,
            StatusRelatorio.REJEITADO,
        },
        StatusRelatorio.AJUSTE: {StatusRelatorio.CONFERENCIA},
    }

    if novo_status not in transicoes.get(relatorio.status, set()):
        erros.append(
            f"Transicao invalida de {relatorio.get_status_display()} para {novo_status}."
        )

    return ResultadoValidacaoOperacional.falha(erros)
