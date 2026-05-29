from django.urls import reverse

from relatorios.models import RelatorioTecnico, StatusRelatorio
from relatorios.services.autorizacao_service import (
    queryset_relatorios_visiveis,
    usuario_pode_atuar_como_financeiro,
)
from relatorios.services.clientes_valor_km_service import (
    clientes_pendentes_valor_km,
    usuario_pode_configurar_valor_km,
)


def _notificacao(tipo, titulo, mensagem, quantidade, url, icone, nivel):
    return {
        "tipo": tipo,
        "titulo": titulo,
        "mensagem": mensagem,
        "quantidade": quantidade,
        "url": url,
        "icone": icone,
        "nivel": nivel,
    }


def _url_relatorios_status(status):
    return f"{reverse('relatorios:relatorio_list')}?status={status}"


def _url_clientes_pendentes_valor_km():
    return f"{reverse('relatorios:cliente_list')}?valor_km=pendente"


def obter_notificacoes_usuario(usuario):
    if not getattr(usuario, "is_authenticated", False):
        return []

    notificacoes = []

    if usuario_pode_atuar_como_financeiro(usuario):
        qtd_conferencia = RelatorioTecnico.objects.filter(
            status=StatusRelatorio.CONFERENCIA
        ).count()
        if qtd_conferencia:
            notificacoes.append(
                _notificacao(
                    "relatorios_conferencia",
                    "Relatórios aguardando conferência",
                    f"Existem {qtd_conferencia} relatório(s) pendente(s) de análise financeira.",
                    qtd_conferencia,
                    _url_relatorios_status(StatusRelatorio.CONFERENCIA),
                    "bi-clipboard-check",
                    "warning",
                )
            )

        qtd_clientes_sem_km = (
            clientes_pendentes_valor_km(usuario, apenas_api_novos=True).count()
            if usuario_pode_configurar_valor_km(usuario)
            else 0
        )
        if qtd_clientes_sem_km:
            notificacoes.append(
                _notificacao(
                    "clientes_sem_valor_km",
                    "Clientes sem valor de KM",
                    f"Existem {qtd_clientes_sem_km} cliente(s) sem valor padrão de quilometragem.",
                    qtd_clientes_sem_km,
                    _url_clientes_pendentes_valor_km(),
                    "bi-speedometer2",
                    "danger",
                )
            )
        return notificacoes

    qs_usuario = queryset_relatorios_visiveis(usuario, RelatorioTecnico.objects.all())
    qtd_ajuste = qs_usuario.filter(status=StatusRelatorio.AJUSTE).count()
    if qtd_ajuste:
        notificacoes.append(
            _notificacao(
                "relatorios_ajuste",
                "Relatórios devolvidos para ajuste",
                f"Existem {qtd_ajuste} relatório(s) aguardando correção.",
                qtd_ajuste,
                _url_relatorios_status(StatusRelatorio.AJUSTE),
                "bi-arrow-repeat",
                "warning",
            )
        )

    qtd_rascunho = qs_usuario.filter(status=StatusRelatorio.RASCUNHO).count()
    if qtd_rascunho:
        notificacoes.append(
            _notificacao(
                "relatorios_rascunho",
                "Relatórios em rascunho",
                f"Você possui {qtd_rascunho} relatório(s) ainda não enviado(s).",
                qtd_rascunho,
                _url_relatorios_status(StatusRelatorio.RASCUNHO),
                "bi-file-earmark-text",
                "secondary",
            )
        )

    return notificacoes


def total_notificacoes(notificacoes):
    return sum(int(item.get("quantidade") or 0) for item in notificacoes or [])
