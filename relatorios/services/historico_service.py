from relatorios.models import HistoricoRelatorio, TipoEventoHistorico


EVENTOS_LEGADOS = {
    "Relatório criado": TipoEventoHistorico.CRIADO,
    "Relatório enviado para conferência": TipoEventoHistorico.ENVIADO,
    "Relatório reenviado para conferência": TipoEventoHistorico.REENVIADO,
    "Financeiro solicitou ajustes": TipoEventoHistorico.AJUSTE_SOLICITADO,
    "Relatório rejeitado definitivamente": TipoEventoHistorico.REJEITADO,
    "Relatório aprovado": TipoEventoHistorico.APROVADO,
    "Despesa rejeitada pelo financeiro": TipoEventoHistorico.ITEM_REJEITADO,
    "Trecho KM rejeitado pelo financeiro": TipoEventoHistorico.ITEM_REJEITADO,
    "Item restaurado pelo financeiro": TipoEventoHistorico.ITEM_REATIVADO,
    "Valor aprovado alterado": TipoEventoHistorico.VALOR_ALTERADO,
    "Email enviado": TipoEventoHistorico.EMAIL_ENVIADO,
    "Falha no envio de email": TipoEventoHistorico.EMAIL_FALHA,
}


def normalizar_tipo_evento(tipo_evento):
    if isinstance(tipo_evento, TipoEventoHistorico):
        return tipo_evento
    if tipo_evento in TipoEventoHistorico.values:
        return tipo_evento
    return EVENTOS_LEGADOS.get(tipo_evento, TipoEventoHistorico.CRIADO)


def registrar_evento(relatorio, usuario, tipo_evento, descricao, dados_json=None):
    tipo_normalizado = normalizar_tipo_evento(tipo_evento)
    return HistoricoRelatorio.objects.create(
        relatorio=relatorio,
        usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
        tipo_evento=tipo_normalizado,
        acao=TipoEventoHistorico(tipo_normalizado).label,
        descricao=descricao,
        dados_json=dados_json or {},
    )
