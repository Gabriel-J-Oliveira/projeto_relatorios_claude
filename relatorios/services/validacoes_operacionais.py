import logging
from dataclasses import dataclass, field
from decimal import Decimal

from relatorios.models import StatusFinanceiroItem, StatusRelatorio
from relatorios.services.financeiro_validator import (
    validar_integridade_financeira_relatorio,
)
from relatorios.services.clientes_valor_km_service import (
    erros_clientes_sem_valor_km_relatorio,
)


logger = logging.getLogger(__name__)

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


def _log_validacao(nome, relatorio, campo, item, mensagem):
    logger.debug(
        "VALIDACAO_OPERACIONAL nome=%s relatorio_id=%s campo=%s item=%s mensagem_original=%s mensagem_exibida=%s",
        nome,
        getattr(relatorio, "pk", None),
        campo,
        item,
        mensagem,
        mensagem,
    )


def _adicionar_erro(erros, relatorio, nome, mensagem, *, campo="", item=""):
    erros.append(mensagem)
    _log_validacao(nome, relatorio, campo, item, mensagem)


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
        if (relatorio.motivo or "").strip():
            return []
        mensagem = "Informe o motivo da viagem para todos os clientes do relatorio."
        _log_validacao(
            "validar_motivos_clientes_relatorio",
            relatorio,
            "motivo",
            f"cliente:{relatorio.cliente_id}",
            mensagem,
        )
        return [mensagem]
    sem_motivo = [
        vinculo.cliente.nome
        for vinculo in vinculos
        if not (vinculo.motivo_viagem or "").strip()
    ]
    if sem_motivo:
        mensagem = "Informe o motivo da viagem para todos os clientes do relatorio."
        _log_validacao(
            "validar_motivos_clientes_relatorio",
            relatorio,
            "motivo_viagem",
            ",".join(sem_motivo),
            mensagem,
        )
        return [mensagem]
    return []


def validar_valores_negativos(relatorio):
    erros = []

    for despesa in relatorio.despesas.all():
        if despesa.valor is not None and despesa.valor < 0:
            _adicionar_erro(
                erros,
                relatorio,
                "validar_valores_negativos",
                f"Despesa {despesa.pk} possui valor solicitado negativo.",
                campo="valor",
                item=f"despesa:{despesa.pk}",
            )
        if despesa.valor_aprovado is not None and despesa.valor_aprovado < 0:
            _adicionar_erro(
                erros,
                relatorio,
                "validar_valores_negativos",
                f"Despesa {despesa.pk} possui valor aprovado negativo.",
                campo="valor_aprovado",
                item=f"despesa:{despesa.pk}",
            )

    for trecho in relatorio.trechos.all():
        if trecho.km is not None and trecho.km < 0:
            _adicionar_erro(
                erros,
                relatorio,
                "validar_valores_negativos",
                f"Trecho KM {trecho.pk} possui quilometragem negativa.",
                campo="km",
                item=f"trecho:{trecho.pk}",
            )
        if trecho.valor_km is not None and trecho.valor_km < 0:
            _adicionar_erro(
                erros,
                relatorio,
                "validar_valores_negativos",
                f"Trecho KM {trecho.pk} possui valor por KM negativo.",
                campo="valor_km",
                item=f"trecho:{trecho.pk}",
            )
        if trecho.valor_km_aprovado is not None and trecho.valor_km_aprovado < 0:
            _adicionar_erro(
                erros,
                relatorio,
                "validar_valores_negativos",
                f"Trecho KM {trecho.pk} possui valor por KM aprovado negativo.",
                campo="valor_km_aprovado",
                item=f"trecho:{trecho.pk}",
            )

    if relatorio.km_excedente_interno is not None and relatorio.km_excedente_interno < 0:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_valores_negativos",
            "KM excedente / deslocamento interno nao pode ser negativo.",
            campo="km_excedente_interno",
        )

    return erros


def validar_relatorio_para_edicao(relatorio):
    if relatorio.status in ESTADOS_FINAIS:
        _log_validacao(
            "validar_relatorio_para_edicao",
            relatorio,
            "status",
            "",
            "Relatorio aprovado ou rejeitado esta bloqueado para alteracoes.",
        )
        return ResultadoValidacaoOperacional.falha(
            ["Relatorio aprovado ou rejeitado esta bloqueado para alteracoes."]
        )
    return ResultadoValidacaoOperacional.sucesso()


def validar_relatorio_para_envio(relatorio):
    erros = []

    if relatorio.status not in {StatusRelatorio.RASCUNHO, StatusRelatorio.AJUSTE}:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_envio",
            "Este relatorio nao pode ser enviado no status atual.",
            campo="status",
        )

    if relatorio.status in ESTADOS_FINAIS:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_envio",
            "Relatorio finalizado nao pode ser reenviado.",
            campo="status",
        )

    if not relatorio_tem_itens_validos(relatorio):
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_envio",
            "Adicione pelo menos uma despesa ou trecho de KM antes de enviar.",
            campo="itens",
        )

    erros.extend(validar_valores_negativos(relatorio))
    erros.extend(validar_motivos_clientes_relatorio(relatorio))
    erros.extend(
        erro
        for erro in validar_integridade_financeira_relatorio(relatorio)
        if "valor/KM" not in erro
    )

    return ResultadoValidacaoOperacional.falha(erros)


def validar_relatorio_para_aprovacao(relatorio):
    erros = []

    if relatorio.status != StatusRelatorio.CONFERENCIA:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_aprovacao",
            "Somente relatorios em conferencia pendente podem ser aprovados.",
            campo="status",
        )

    if relatorio.status in ESTADOS_FINAIS:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_aprovacao",
            "Relatorio finalizado nao pode sofrer nova aprovacao.",
            campo="status",
        )

    if not relatorio_tem_itens_validos(relatorio):
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_aprovacao",
            "O relatorio nao possui despesas ou trechos de KM.",
            campo="itens",
        )

    if not relatorio_tem_itens_ativos(relatorio):
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_aprovacao",
            "Todos os itens do relatorio estao rejeitados ou inativos.",
            campo="itens",
        )

    total_aprovado = calcular_total_aprovado(relatorio)
    if total_aprovado <= Decimal("0.00"):
        _adicionar_erro(
            erros,
            relatorio,
            "validar_relatorio_para_aprovacao",
            "O valor aprovado total e R$ 0,00.",
            campo="total_aprovado",
        )

    erros.extend(validar_valores_negativos(relatorio))
    erros.extend(validar_motivos_clientes_relatorio(relatorio))
    erros.extend(erros_clientes_sem_valor_km_relatorio(relatorio))
    erros.extend(validar_integridade_financeira_relatorio(relatorio))

    return ResultadoValidacaoOperacional.falha(erros)


def validar_transicao_status(relatorio, novo_status):
    erros = []

    if relatorio.status in ESTADOS_FINAIS:
        _adicionar_erro(
            erros,
            relatorio,
            "validar_transicao_status",
            "Relatorio aprovado ou rejeitado esta bloqueado para alteracoes.",
            campo="status",
        )

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
        _adicionar_erro(
            erros,
            relatorio,
            "validar_transicao_status",
            f"Transicao invalida de {relatorio.get_status_display()} para {novo_status}.",
            campo="status",
        )

    return ResultadoValidacaoOperacional.falha(erros)
