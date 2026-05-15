import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from relatorios.models import (
    Adiantamento,
    ItemDespesa,
    RelatorioTecnico,
    SequencialRelatorio,
    StatusFinanceiroItem,
    StatusRelatorio,
    TipoAdiantamento,
    TipoEventoHistorico,
    TrechoKm,
)
from relatorios.services.historico_service import registrar_evento


CHAVE_SEQUENCIAL_RELATORIO = "relatorio_oficial"
ESTADOS_FINAIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


class WorkflowError(Exception):
    pass


def relatorio_bloqueado(relatorio):
    return relatorio.status in ESTADOS_FINAIS


def _formatar_moeda(valor):
    valor = valor or Decimal("0.00")
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _parse_decimal_financeiro(valor):
    valor = (valor or "").strip()
    if not valor:
        return None

    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")

    try:
        numero = Decimal(valor).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise WorkflowError("Valor aprovado inválido.")

    if numero < 0:
        raise WorkflowError("Valor aprovado não pode ser negativo.")

    return numero


def _extrair_sequencial(numero):
    match = re.search(r"(\d+)$", str(numero or ""))
    return int(match.group(1)) if match else 0


def _proximo_numero_existente():
    maior = 0
    for numero in RelatorioTecnico.objects.exclude(numero__isnull=True).values_list(
        "numero", flat=True
    ):
        maior = max(maior, _extrair_sequencial(numero))
    return maior + 1


def _obter_contador_bloqueado():
    try:
        return SequencialRelatorio.objects.select_for_update().get(
            chave=CHAVE_SEQUENCIAL_RELATORIO
        )
    except SequencialRelatorio.DoesNotExist:
        try:
            SequencialRelatorio.objects.create(
                chave=CHAVE_SEQUENCIAL_RELATORIO,
                proximo_numero=_proximo_numero_existente(),
            )
        except IntegrityError:
            pass
        return SequencialRelatorio.objects.select_for_update().get(
            chave=CHAVE_SEQUENCIAL_RELATORIO
        )


def gerar_numero_oficial(relatorio):
    if relatorio.numero:
        return relatorio.numero

    contador = _obter_contador_bloqueado()
    minimo_seguro = _proximo_numero_existente()
    if contador.proximo_numero < minimo_seguro:
        contador.proximo_numero = minimo_seguro
        contador.save(update_fields=["proximo_numero", "atualizado_em"])
    while True:
        numero = str(contador.proximo_numero)
        contador.proximo_numero += 1
        contador.save(update_fields=["proximo_numero", "atualizado_em"])
        if not RelatorioTecnico.objects.filter(numero=numero).exists():
            relatorio.numero = numero
            relatorio.save(update_fields=["numero", "atualizado_em"])
            return numero


def validar_transicao(relatorio, novo_status):
    if relatorio.status in ESTADOS_FINAIS:
        raise WorkflowError("Relatório aprovado ou rejeitado está bloqueado para alterações.")

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
        raise WorkflowError(
            f"Transição inválida de {relatorio.get_status_display()} para {novo_status}."
        )


def _registrar_adiantamento_do_relatorio(relatorio):
    valor = relatorio.valor_adiantamento or Decimal("0.00")
    if valor <= 0:
        return

    descricao = f"Adiantamento vinculado ao relatório {relatorio.numero}"
    adiantamento = (
        Adiantamento.objects.select_for_update()
        .filter(relatorio=relatorio, tipo=TipoAdiantamento.ADIANTAMENTO)
        .order_by("pk")
        .first()
    )

    if adiantamento:
        adiantamento.tecnico = relatorio.tecnico_responsavel
        adiantamento.valor = valor
        adiantamento.data = relatorio.data_inicio
        adiantamento.descricao = descricao
        adiantamento.save(update_fields=["tecnico", "valor", "data", "descricao"])
        return

    Adiantamento.objects.create(
        tecnico=relatorio.tecnico_responsavel,
        relatorio=relatorio,
        tipo=TipoAdiantamento.ADIANTAMENTO,
        valor=valor,
        data=relatorio.data_inicio,
        descricao=descricao,
    )


def _salvar_valores_aprovados(post_data, relatorio, usuario, consolidar=False):
    despesas = list(relatorio.despesas.select_for_update())
    trechos = list(relatorio.trechos.select_for_update())
    novos_valores_despesas = []
    novos_valores_trechos = []

    for despesa in despesas:
        nome_campo = f"despesa_{despesa.pk}_valor_aprovado"
        if despesa.rejeitado or despesa.status_financeiro == StatusFinanceiroItem.REJEITADO:
            valor_aprovado = Decimal("0.00") if consolidar else despesa.valor_aprovado
        else:
            valor_aprovado = _parse_decimal_financeiro(post_data.get(nome_campo))
        if consolidar and valor_aprovado is None:
            valor_aprovado = despesa.valor
        novos_valores_despesas.append((despesa, valor_aprovado))

    for trecho in trechos:
        nome_campo = f"trecho_{trecho.pk}_valor_km_aprovado"
        if trecho.rejeitado or trecho.status_financeiro == StatusFinanceiroItem.REJEITADO:
            valor_km_aprovado = Decimal("0.00") if consolidar else trecho.valor_km_aprovado
        else:
            valor_km_aprovado = _parse_decimal_financeiro(post_data.get(nome_campo))
        if consolidar and valor_km_aprovado is None:
            valor_km_aprovado = trecho.valor_km.quantize(Decimal("0.01"))
        novos_valores_trechos.append((trecho, valor_km_aprovado))

    for despesa, valor_aprovado in novos_valores_despesas:
        if despesa.valor_aprovado != valor_aprovado:
            valor_anterior = despesa.valor_aprovado
            despesa.valor_aprovado = valor_aprovado
            despesa.save(update_fields=["valor_aprovado"])
            registrar_evento(
                relatorio,
                usuario,
                TipoEventoHistorico.VALOR_ALTERADO,
                (
                    f"Valor aprovado da despesa {despesa.pk} alterado de "
                    f"{_formatar_moeda(valor_anterior)} para {_formatar_moeda(valor_aprovado)}."
                ),
                {
                    "tipo_item": "despesa",
                    "item_id": despesa.pk,
                    "valor_anterior": str(valor_anterior or ""),
                    "valor_novo": str(valor_aprovado or ""),
                },
            )

    for trecho, valor_km_aprovado in novos_valores_trechos:
        if trecho.valor_km_aprovado != valor_km_aprovado:
            valor_anterior = trecho.valor_km_aprovado
            trecho.valor_km_aprovado = valor_km_aprovado
            trecho.save(update_fields=["valor_km_aprovado"])
            registrar_evento(
                relatorio,
                usuario,
                TipoEventoHistorico.VALOR_ALTERADO,
                (
                    f"Valor por KM aprovado do trecho {trecho.pk} alterado de "
                    f"{_formatar_moeda(valor_anterior)} para {_formatar_moeda(valor_km_aprovado)}."
                ),
                {
                    "tipo_item": "trecho",
                    "item_id": trecho.pk,
                    "valor_anterior": str(valor_anterior or ""),
                    "valor_novo": str(valor_km_aprovado or ""),
                },
            )


def _validar_aprovacao_financeira(relatorio):
    if not relatorio.despesas.exists() and not relatorio.trechos.exists():
        raise WorkflowError("Adicione pelo menos uma despesa ou trecho de KM.")
    if relatorio.total_aprovado <= 0:
        raise WorkflowError("Não é possível aprovar relatório com total aprovado zerado.")
    itens_ativos = any(
        not item.rejeitado and item.status_financeiro != StatusFinanceiroItem.REJEITADO
        for item in list(relatorio.despesas.all()) + list(relatorio.trechos.all())
    )
    if not itens_ativos:
        raise WorkflowError("Não é possível aprovar relatório com todos os itens rejeitados.")


def _usuario(usuario):
    return usuario if getattr(usuario, "is_authenticated", False) else None


def enviar_para_conferencia(relatorio_id, usuario=None):
    with transaction.atomic():
        relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
        validar_transicao(relatorio, StatusRelatorio.CONFERENCIA)
        erros = relatorio.pode_enviar()
        if erros:
            raise WorkflowError(erros[0])
        status_anterior = relatorio.status
        gerar_numero_oficial(relatorio)
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status", "atualizado_em"])
        tipo_evento = (
            TipoEventoHistorico.REENVIADO
            if status_anterior == StatusRelatorio.AJUSTE
            else TipoEventoHistorico.ENVIADO
        )
        registrar_evento(
            relatorio,
            _usuario(usuario),
            tipo_evento,
            f"Relatório {relatorio.numero} enviado para conferência.",
            {"status_anterior": status_anterior, "status_novo": relatorio.status},
        )
        return relatorio


def solicitar_ajuste(relatorio_id, usuario=None, justificativa=""):
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise WorkflowError("Informe a justificativa para esta ação.")
    with transaction.atomic():
        relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
        validar_transicao(relatorio, StatusRelatorio.AJUSTE)
        relatorio.status = StatusRelatorio.AJUSTE
        relatorio.motivo_rejeicao = justificativa
        relatorio.save(update_fields=["status", "motivo_rejeicao", "atualizado_em"])
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.AJUSTE_SOLICITADO,
            f"Financeiro solicitou correções: {justificativa}",
            {"motivo": justificativa},
        )
        return relatorio


def aprovar_relatorio(relatorio_id, usuario=None, post_data=None):
    with transaction.atomic():
        relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
        validar_transicao(relatorio, StatusRelatorio.APROVADO)
        _salvar_valores_aprovados(post_data or {}, relatorio, _usuario(usuario), consolidar=True)
        _validar_aprovacao_financeira(relatorio)
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_em = timezone.now()
        relatorio.aprovado_por = _usuario(usuario)
        relatorio.save(update_fields=["status", "aprovado_em", "aprovado_por", "atualizado_em"])
        _registrar_adiantamento_do_relatorio(relatorio)
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.APROVADO,
            f"Relatório {relatorio.numero} aprovado.",
            {
                "total_aprovado": str(relatorio.total_aprovado),
                "diferenca_removida": str(relatorio.diferenca_removida),
            },
        )
        return relatorio


def rejeitar_relatorio(relatorio_id, usuario=None, justificativa=""):
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise WorkflowError("Informe a justificativa para esta ação.")
    with transaction.atomic():
        relatorio = RelatorioTecnico.objects.select_for_update().get(pk=relatorio_id)
        validar_transicao(relatorio, StatusRelatorio.REJEITADO)
        relatorio.status = StatusRelatorio.REJEITADO
        relatorio.motivo_rejeicao = justificativa
        relatorio.save(update_fields=["status", "motivo_rejeicao", "atualizado_em"])
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.REJEITADO,
            f"Relatório rejeitado definitivamente: {justificativa}",
            {"motivo": justificativa},
        )
        return relatorio
