import logging
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from relatorios.models import TipoEventoHistorico
from relatorios.services.autorizacao_service import (
    GRUPO_ADMIN_ERP,
    GRUPO_DOMAIN_ADMINS,
    GRUPO_FINANCEIRO,
    GRUPO_GESTOR,
)
from relatorios.services.historico_service import registrar_evento
from relatorios.services.pdf_cliente_service import gerar_zip_pdfs_clientes
from relatorios.services.pdf_interno_service import montar_contexto_pdf_interno


logger = logging.getLogger(__name__)


class EmailNotificacaoError(Exception):
    pass


GRUPOS_DESTINATARIOS_FINANCEIRO = [
    GRUPO_FINANCEIRO,
    GRUPO_GESTOR,
    GRUPO_ADMIN_ERP,
    GRUPO_DOMAIN_ADMINS,
]


def _email_valido(email):
    email = (email or "").strip()
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        return ""
    return email.lower()


def _unicos(emails):
    resultado = []
    vistos = set()
    for email in emails:
        normalizado = _email_valido(email)
        if normalizado and normalizado not in vistos:
            vistos.add(normalizado)
            resultado.append(normalizado)
    return resultado


def _formatar_moeda(valor):
    valor = valor or 0
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_data(data):
    return data.strftime("%d/%m/%Y") if data else "Não informado"


def _clientes_texto(relatorio):
    clientes = [cliente.nome for cliente in relatorio.clientes_exibicao()]
    if not clientes and relatorio.cliente:
        clientes = [relatorio.cliente.nome]
    return ", ".join(clientes) or "Não informado"


def _tecnicos_texto(relatorio):
    tecnicos = [tecnico.nome for tecnico in relatorio.tecnicos_exibicao()]
    if not tecnicos and relatorio.tecnico_responsavel:
        tecnicos = [relatorio.tecnico_responsavel.nome]
    return ", ".join(tecnicos) or "Não informado"


def _link_relatorio(relatorio):
    caminho = reverse("relatorios:relatorio_consulta", kwargs={"pk": relatorio.pk})
    base_url = (getattr(settings, "APP_BASE_URL", "") or "").strip()
    if not base_url:
        return caminho
    return urljoin(base_url.rstrip("/") + "/", caminho.lstrip("/"))


def get_destinatarios_financeiro():
    User = get_user_model()
    usuarios = (
        User.objects.filter(
            is_active=True,
            groups__name__in=GRUPOS_DESTINATARIOS_FINANCEIRO,
        )
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    return _unicos(usuarios)


def get_destinatarios_tecnicos(relatorio):
    emails = []
    for tecnico in relatorio.tecnicos_exibicao():
        if getattr(tecnico, "ativo", True):
            emails.append(tecnico.email)
    if relatorio.criado_por and relatorio.criado_por.is_active:
        emails.append(relatorio.criado_por.email)
    return _unicos(emails)


def get_destinatarios_internos_finalizacao(relatorio):
    return _unicos(
        get_destinatarios_financeiro()
        + get_destinatarios_tecnicos(relatorio)
        + list(getattr(settings, "EMAIL_DESTINATARIOS_FINALIZACAO_EXTRA", []) or [])
    )


def _registrar_email(relatorio, tipo_email, destinatarios, anexos=None, erro=None):
    if not relatorio:
        return None
    sucesso = erro is None
    tipo_evento = (
        TipoEventoHistorico.EMAIL_ENVIADO
        if sucesso
        else TipoEventoHistorico.EMAIL_FALHA
    )
    descricao = (
        f"Email interno enviado ({tipo_email}) para {len(destinatarios)} destinatário(s)."
        if sucesso
        else f"Falha no envio de email interno ({tipo_email}): {erro}"
    )
    return registrar_evento(
        relatorio,
        None,
        tipo_evento,
        descricao,
        {
            "tipo_email": tipo_email,
            "destinatarios": list(destinatarios),
            "anexos": list(anexos or []),
            "erro": str(erro or ""),
        },
    )


def enviar_email_base(
    assunto,
    corpo,
    destinatarios,
    *,
    relatorio=None,
    tipo_email="notificacao",
    anexos=None,
):
    destinatarios = _unicos(destinatarios)
    anexos = list(anexos or [])
    nomes_anexos = [anexo[0] for anexo in anexos]

    if not destinatarios:
        erro = "Nenhum destinatário interno válido encontrado."
        logger.warning(
            "Email interno %s do relatorio %s sem destinatarios validos.",
            tipo_email,
            getattr(relatorio, "pk", None),
        )
        _registrar_email(relatorio, tipo_email, destinatarios, nomes_anexos, erro=erro)
        raise EmailNotificacaoError(erro)

    try:
        logger.info(
            "Iniciando envio de email interno %s do relatorio %s para %s destinatario(s).",
            tipo_email,
            getattr(relatorio, "pk", None),
            len(destinatarios),
        )
        email = EmailMultiAlternatives(
            subject=assunto,
            body=corpo,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=destinatarios,
        )
        for nome, conteudo, mimetype in anexos:
            email.attach(nome, conteudo, mimetype)
        enviados = email.send(fail_silently=False)
    except Exception as exc:
        logger.exception(
            "Falha ao enviar email interno %s do relatorio %s.",
            tipo_email,
            getattr(relatorio, "pk", None),
        )
        _registrar_email(relatorio, tipo_email, destinatarios, nomes_anexos, erro=exc)
        raise EmailNotificacaoError(str(exc)) from exc

    logger.info(
        "Email interno %s enviado para %s destinatario(s) no relatorio %s.",
        tipo_email,
        len(destinatarios),
        getattr(relatorio, "pk", None),
    )
    _registrar_email(relatorio, tipo_email, destinatarios, nomes_anexos)
    return enviados


def _corpo_base(relatorio, mensagem):
    return "\n".join(
        [
            "Olá.",
            "",
            mensagem,
            "",
            f"Relatório: #{relatorio.identificador}",
            f"Cliente(s): {_clientes_texto(relatorio)}",
            f"Técnico(s): {_tecnicos_texto(relatorio)}",
            f"Período: {_formatar_data(relatorio.data_inicio)} a {_formatar_data(relatorio.data_fim)}",
            f"Status: {relatorio.get_status_display()}",
            "",
            f"Abrir relatório: {_link_relatorio(relatorio)}",
            "",
            "Mensagem automática do sistema de relatórios.",
        ]
    )


def notificar_relatorio_enviado(relatorio):
    return enviar_email_base(
        f"Relatório enviado para conferência — #{relatorio.identificador}",
        _corpo_base(relatorio, "Um relatório foi enviado para conferência financeira."),
        get_destinatarios_financeiro(),
        relatorio=relatorio,
        tipo_email="relatorio_enviado",
    )


def notificar_relatorio_reenviado(relatorio):
    return enviar_email_base(
        f"Relatório reenviado para conferência — #{relatorio.identificador}",
        _corpo_base(relatorio, "Um relatório em ajuste foi reenviado para conferência financeira."),
        get_destinatarios_financeiro(),
        relatorio=relatorio,
        tipo_email="relatorio_reenviado",
    )


def notificar_ajuste_solicitado(relatorio):
    motivo = (relatorio.motivo_rejeicao or "").strip()
    corpo = _corpo_base(
        relatorio,
        "O financeiro solicitou ajustes neste relatório."
        + (f"\n\nJustificativa: {motivo}" if motivo else ""),
    )
    return enviar_email_base(
        f"Ajuste solicitado no relatório — #{relatorio.identificador}",
        corpo,
        get_destinatarios_tecnicos(relatorio),
        relatorio=relatorio,
        tipo_email="ajuste_solicitado",
    )


def notificar_relatorio_rejeitado(relatorio):
    motivo = (relatorio.motivo_rejeicao or "").strip()
    corpo = _corpo_base(
        relatorio,
        "O relatório foi rejeitado definitivamente."
        + (f"\n\nJustificativa: {motivo}" if motivo else ""),
    )
    return enviar_email_base(
        f"Relatório rejeitado — #{relatorio.identificador}",
        corpo,
        get_destinatarios_tecnicos(relatorio),
        relatorio=relatorio,
        tipo_email="relatorio_rejeitado",
    )


def _gerar_pdf_interno(relatorio):
    logger.info("Gerando PDF interno para envio por email do relatorio %s.", relatorio.pk)
    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        logger.exception("WeasyPrint indisponivel ao gerar PDF interno do relatorio %s.", relatorio.pk)
        raise EmailNotificacaoError("WeasyPrint indisponível para gerar PDF interno.") from exc

    emitido_em = timezone.localtime(timezone.now())
    contexto = montar_contexto_pdf_interno(
        relatorio,
        emitido_em,
        usuario_gerador=None,
        avisos_financeiro=[],
    )
    html = render_to_string(
        "relatorios/pdf/interno.html",
        {
            "pdf": contexto,
            "empresa": "CONTROL SUL GESTÃO EMPRESARIAL",
            "emitido_em": emitido_em,
            "usuario_gerador": None,
        },
    )
    css_path = settings.BASE_DIR / "static" / "css" / "pdf-relatorio.css"
    base_url = getattr(settings, "APP_BASE_URL", "") or str(settings.BASE_DIR)
    return HTML(string=html, encoding="utf-8", base_url=base_url).write_pdf(
        stylesheets=[CSS(filename=str(css_path))]
    )


def enviar_documentos_relatorio_finalizado(relatorio):
    logger.info("Gerando anexos de finalizacao do relatorio %s.", relatorio.pk)
    pdf_interno = _gerar_pdf_interno(relatorio)
    zip_clientes, gerados, ignorados = gerar_zip_pdfs_clientes(relatorio)
    anexos = [
        (
            f"relatorio-interno-{relatorio.numero}.pdf",
            pdf_interno,
            "application/pdf",
        ),
        (
            f"relatorio-{relatorio.numero}-clientes.zip",
            zip_clientes,
            "application/zip",
        ),
    ]

    corpo = "\n".join(
        [
            "Olá.",
            "",
            "O relatório foi finalizado e os documentos oficiais foram gerados.",
            "",
            f"Relatório: #{relatorio.identificador}",
            f"Cliente(s): {_clientes_texto(relatorio)}",
            f"Técnico responsável: {relatorio.tecnico_principal_exibicao() or 'Não informado'}",
            f"Período: {_formatar_data(relatorio.data_inicio)} a {_formatar_data(relatorio.data_fim)}",
            "Status final: Aprovado",
            f"Total aprovado: {_formatar_moeda(relatorio.total_aprovado)}",
            "",
            "Arquivos anexados:",
            "- Relatório financeiro interno",
            "- Pacote ZIP com os PDFs individuais dos clientes",
            "",
            f"Abrir relatório: {_link_relatorio(relatorio)}",
            "",
            "Mensagem automática do sistema de relatórios.",
        ]
    )
    enviados = enviar_email_base(
        "Relatório finalizado — documentos gerados",
        corpo,
        get_destinatarios_internos_finalizacao(relatorio),
        relatorio=relatorio,
        tipo_email="relatorio_finalizado_documentos",
        anexos=anexos,
    )
    if ignorados:
        logger.info(
            "PDFs de clientes ignorados no email do relatorio %s: %s",
            relatorio.pk,
            ignorados,
        )
    logger.info(
        "Email de finalizacao do relatorio %s anexou %s PDF(s) de cliente no ZIP.",
        relatorio.pk,
        len(gerados),
    )
    return enviados


def notificar_relatorio_aprovado(relatorio):
    return enviar_documentos_relatorio_finalizado(relatorio)
