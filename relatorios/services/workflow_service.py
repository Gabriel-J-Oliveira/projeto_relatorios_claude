import logging
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from relatorios.models import (
    Adiantamento,
    RelatorioTecnico,
    SequencialRelatorio,
    StatusFinanceiroItem,
    StatusRelatorio,
    TipoAdiantamento,
    TipoEventoHistorico,
    TrechoKm,
)
from relatorios.services.autorizacao_service import (
    usuario_pode_atuar_como_financeiro,
    usuario_pode_enviar_relatorio,
)
from relatorios.services.historico_service import registrar_evento
from relatorios.services.rateio_service import (
    RateioError,
    garantir_rateio_despesa,
    garantir_rateio_trecho,
    garantir_rateios_relatorio,
    validar_rateios_relatorio,
)
from relatorios.services.snapshot_service import SnapshotError, criar_snapshot_financeiro
from relatorios.services.validacoes_operacionais import (
    validar_relatorio_para_aprovacao,
    validar_relatorio_para_envio,
    validar_transicao_status,
)


CHAVE_SEQUENCIAL_RELATORIO = "relatorio_oficial"
ESTADOS_FINAIS = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}
logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    def __init__(self, errors):
        if isinstance(errors, (list, tuple, set)):
            self.errors = [str(erro) for erro in errors if str(erro).strip()]
        else:
            self.errors = [str(errors)] if str(errors).strip() else []
        super().__init__(" ".join(self.errors))


def relatorio_bloqueado(relatorio):
    return relatorio.status in ESTADOS_FINAIS


def _obter_relatorio_bloqueado(relatorio_ou_id):
    pk = getattr(relatorio_ou_id, "pk", relatorio_ou_id)
    return RelatorioTecnico.objects.select_for_update().get(pk=pk)


def _usuario(usuario):
    return usuario if getattr(usuario, "is_authenticated", False) else None


def _executar_notificacao(relatorio, funcao, aviso):
    try:
        funcao(relatorio)
    except Exception as exc:
        logger.exception(
            "Falha ao executar notificacao de email do relatorio %s: %s",
            getattr(relatorio, "pk", None),
            exc,
        )
        relatorio._email_warning = aviso


def _validar_permissao_envio(relatorio, usuario):
    if not usuario_pode_enviar_relatorio(usuario, relatorio):
        raise WorkflowError("Você não tem permissão para enviar este relatório.")


def _validar_permissao_financeira(usuario):
    if not usuario_pode_atuar_como_financeiro(usuario):
        raise WorkflowError("Você não tem permissão para executar esta ação financeira.")


def _aplicar_status(relatorio, novo_status, update_fields=None):
    relatorio.status = novo_status
    campos = ["status", "atualizado_em"]
    if update_fields:
        campos.extend(update_fields)
    relatorio.save(update_fields=list(dict.fromkeys(campos)))
    return relatorio


def preparar_rascunho_para_salvar(relatorio, instance=None):
    """
    Mantem criacao/edicao de rascunho sem espalhar atribuicao de status na view.
    Nao registra historico porque nao representa transicao operacional.
    """
    if instance:
        relatorio.status = instance.status
    elif not relatorio.status:
        relatorio.status = StatusRelatorio.RASCUNHO
    return relatorio


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
    resultado = validar_transicao_status(relatorio, novo_status)
    if not resultado.ok:
        logger.warning(
            "Transicao de status bloqueada no relatorio %s: %s -> %s | erros=%s",
            relatorio.pk,
            relatorio.status,
            novo_status,
            resultado.errors,
        )
        raise WorkflowError(resultado.errors)


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
        valor_postado = post_data.get(nome_campo)
        valor_postado_preenchido = str(valor_postado or "").strip() != ""
        if despesa.rejeitado or despesa.status_financeiro == StatusFinanceiroItem.REJEITADO:
            valor_aprovado = Decimal("0.00") if consolidar else despesa.valor_aprovado
        else:
            valor_aprovado = _parse_decimal_financeiro(valor_postado)
        if consolidar and valor_aprovado is None:
            valor_aprovado = despesa.valor
        registrar_alteracao = (
            valor_postado_preenchido
            and valor_aprovado is not None
            and valor_aprovado != despesa.valor
        ) or (
            despesa.valor_aprovado is not None
            and despesa.valor_aprovado != valor_aprovado
        )
        novos_valores_despesas.append((despesa, valor_aprovado, registrar_alteracao))

    for trecho in trechos:
        if trecho.tem_multiplos_clientes:
            continue
        nome_campo = f"trecho_{trecho.pk}_valor_km_aprovado"
        valor_postado = post_data.get(nome_campo)
        valor_postado_preenchido = str(valor_postado or "").strip() != ""
        if trecho.rejeitado or trecho.status_financeiro == StatusFinanceiroItem.REJEITADO:
            valor_km_aprovado = Decimal("0.00") if consolidar else trecho.valor_km_aprovado
        else:
            valor_km_aprovado = _parse_decimal_financeiro(valor_postado)
        if consolidar and valor_km_aprovado is None:
            valor_km_aprovado = trecho.valor_km.quantize(Decimal("0.01"))
        registrar_alteracao = (
            valor_postado_preenchido
            and valor_km_aprovado is not None
            and valor_km_aprovado != trecho.valor_km.quantize(Decimal("0.01"))
        ) or (
            trecho.valor_km_aprovado is not None
            and trecho.valor_km_aprovado != valor_km_aprovado
        )
        novos_valores_trechos.append((trecho, valor_km_aprovado, registrar_alteracao))

    for despesa, valor_aprovado, registrar_alteracao in novos_valores_despesas:
        if despesa.valor_aprovado != valor_aprovado:
            valor_anterior = despesa.valor_aprovado
            despesa.valor_aprovado = valor_aprovado
            despesa.save(update_fields=["valor_aprovado"])
            try:
                garantir_rateio_despesa(despesa)
            except RateioError as exc:
                raise WorkflowError(f"Despesa {despesa.pk}: {exc}") from exc
            if not registrar_alteracao:
                continue
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

    for trecho, valor_km_aprovado, registrar_alteracao in novos_valores_trechos:
        if trecho.valor_km_aprovado != valor_km_aprovado:
            valor_anterior = trecho.valor_km_aprovado
            trecho.valor_km_aprovado = valor_km_aprovado
            trecho.save(update_fields=["valor_km_aprovado"])
            try:
                garantir_rateio_trecho(trecho)
            except RateioError as exc:
                raise WorkflowError(f"Trecho KM {trecho.pk}: {exc}") from exc
            if not registrar_alteracao:
                continue
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
    try:
        garantir_rateios_relatorio(relatorio)
    except RateioError as exc:
        raise WorkflowError(str(exc)) from exc

    resultado = validar_relatorio_para_aprovacao(relatorio)
    if not resultado.ok:
        raise WorkflowError(resultado.errors)

    if (
        not relatorio.despesas.exists()
        and not relatorio.trechos.exists()
        and (relatorio.km_excedente_interno or Decimal("0.00")) <= 0
    ):
        raise WorkflowError("Adicione pelo menos uma despesa ou trecho de KM.")
    if relatorio.total_aprovado <= 0:
        raise WorkflowError("Não é possível aprovar relatório com total aprovado zerado.")
    itens_ativos = any(
        not item.rejeitado and item.status_financeiro != StatusFinanceiroItem.REJEITADO
        for item in list(relatorio.despesas.all()) + list(relatorio.trechos.all())
    )
    if not itens_ativos and (relatorio.km_excedente_interno or Decimal("0.00")) <= 0:
        raise WorkflowError("N?o ? poss?vel aprovar relat?rio com todos os itens rejeitados.")
    erros_rateio = validar_rateios_relatorio(relatorio)
    if erros_rateio:
        raise WorkflowError(erros_rateio)


def enviar_para_conferencia(relatorio_id, usuario=None):
    with transaction.atomic():
        relatorio = _obter_relatorio_bloqueado(relatorio_id)
        _validar_permissao_envio(relatorio, usuario)
        validar_transicao(relatorio, StatusRelatorio.CONFERENCIA)
        try:
            garantir_rateios_relatorio(relatorio)
        except RateioError as exc:
            raise WorkflowError(str(exc)) from exc
        resultado_envio = validar_relatorio_para_envio(relatorio)
        if not resultado_envio.ok:
            raise WorkflowError(resultado_envio.errors)
        status_anterior = relatorio.status
        gerar_numero_oficial(relatorio)
        _aplicar_status(relatorio, StatusRelatorio.CONFERENCIA)
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
        logger.info(
            "Relatorio %s enviado para conferencia. Status anterior=%s novo=%s.",
            relatorio.pk,
            status_anterior,
            relatorio.status,
        )
    from relatorios.services.email_service import (
        notificar_relatorio_enviado,
        notificar_relatorio_reenviado,
    )

    _executar_notificacao(
        relatorio,
        notificar_relatorio_reenviado
        if tipo_evento == TipoEventoHistorico.REENVIADO
        else notificar_relatorio_enviado,
        "Relatório enviado, mas houve falha no envio do email.",
    )
    return relatorio


def solicitar_ajuste(relatorio_id, usuario=None, justificativa=""):
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise WorkflowError("Informe a justificativa para esta ação.")
    with transaction.atomic():
        relatorio = _obter_relatorio_bloqueado(relatorio_id)
        _validar_permissao_financeira(usuario)
        validar_transicao(relatorio, StatusRelatorio.AJUSTE)
        status_anterior = relatorio.status
        relatorio.motivo_rejeicao = justificativa
        _aplicar_status(relatorio, StatusRelatorio.AJUSTE, ["motivo_rejeicao"])
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.AJUSTE_SOLICITADO,
            f"Financeiro solicitou correções: {justificativa}",
            {
                "motivo": justificativa,
                "status_anterior": status_anterior,
                "status_novo": relatorio.status,
            },
        )
        logger.info(
            "Ajuste solicitado no relatorio %s. Status anterior=%s novo=%s.",
            relatorio.pk,
            status_anterior,
            relatorio.status,
        )
    from relatorios.services.email_service import notificar_ajuste_solicitado

    _executar_notificacao(
        relatorio,
        notificar_ajuste_solicitado,
        "Ajuste solicitado, mas houve falha no envio do email.",
    )
    return relatorio


def aprovar_relatorio(relatorio_id, usuario=None, post_data=None):
    with transaction.atomic():
        relatorio = _obter_relatorio_bloqueado(relatorio_id)
        _validar_permissao_financeira(usuario)
        validar_transicao(relatorio, StatusRelatorio.APROVADO)
        status_anterior = relatorio.status
        _salvar_valores_aprovados(post_data or {}, relatorio, _usuario(usuario), consolidar=True)
        _validar_aprovacao_financeira(relatorio)
        relatorio.aprovado_em = timezone.now()
        relatorio.aprovado_por = _usuario(usuario)
        _aplicar_status(relatorio, StatusRelatorio.APROVADO, ["aprovado_em", "aprovado_por"])
        _registrar_adiantamento_do_relatorio(relatorio)
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.APROVADO,
            f"Relatório {relatorio.numero} aprovado.",
            {
                "total_aprovado": str(relatorio.total_aprovado),
                "diferenca_removida": str(relatorio.diferenca_removida),
                "status_anterior": status_anterior,
                "status_novo": relatorio.status,
            },
        )
        try:
            criar_snapshot_financeiro(relatorio, _usuario(usuario))
        except SnapshotError as exc:
            raise WorkflowError(exc.args[0]) from exc
        logger.info(
            "Relatorio %s aprovado. Status anterior=%s total_aprovado=%s.",
            relatorio.pk,
            status_anterior,
            relatorio.total_aprovado,
        )
    from relatorios.services.email_service import notificar_relatorio_aprovado

    _executar_notificacao(
        relatorio,
        notificar_relatorio_aprovado,
        "Relatório aprovado, mas houve falha no envio do email.",
    )
    return relatorio


def rejeitar_relatorio(relatorio_id, usuario=None, justificativa=""):
    justificativa = (justificativa or "").strip()
    if not justificativa:
        raise WorkflowError("Informe a justificativa para esta ação.")
    with transaction.atomic():
        relatorio = _obter_relatorio_bloqueado(relatorio_id)
        _validar_permissao_financeira(usuario)
        validar_transicao(relatorio, StatusRelatorio.REJEITADO)
        try:
            garantir_rateios_relatorio(relatorio)
        except RateioError as exc:
            raise WorkflowError(str(exc)) from exc
        status_anterior = relatorio.status
        relatorio.motivo_rejeicao = justificativa
        _aplicar_status(relatorio, StatusRelatorio.REJEITADO, ["motivo_rejeicao"])
        registrar_evento(
            relatorio,
            _usuario(usuario),
            TipoEventoHistorico.REJEITADO,
            f"Relatório rejeitado definitivamente: {justificativa}",
            {
                "motivo": justificativa,
                "status_anterior": status_anterior,
                "status_novo": relatorio.status,
            },
        )
        try:
            criar_snapshot_financeiro(relatorio, _usuario(usuario))
        except SnapshotError as exc:
            raise WorkflowError(exc.args[0]) from exc
        logger.info(
            "Relatorio %s rejeitado definitivamente. Status anterior=%s.",
            relatorio.pk,
            status_anterior,
        )
    from relatorios.services.email_service import notificar_relatorio_rejeitado

    _executar_notificacao(
        relatorio,
        notificar_relatorio_rejeitado,
        "Relatório rejeitado, mas houve falha no envio do email.",
    )
    return relatorio
