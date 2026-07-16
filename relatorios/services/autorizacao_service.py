import logging
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from relatorios.models import StatusRelatorio


logger = logging.getLogger(__name__)
_EXTRA_ADMIN_LOGADOS = set()


GRUPO_FINANCEIRO = "Financeiro"
GRUPO_TECNICO = "Tecnico"
GRUPO_GESTOR = "Gestor"
GRUPO_ADMIN_ERP = "Administrador ERP"
GRUPO_DOMAIN_ADMINS = "Domain Admins"

GRUPOS_ERP = [
    GRUPO_FINANCEIRO,
    GRUPO_TECNICO,
    GRUPO_GESTOR,
    GRUPO_ADMIN_ERP,
    GRUPO_DOMAIN_ADMINS,
]


def _normalizar_login_usuario(valor):
    login = str(valor or "").strip().lower()
    if "\\" in login:
        login = login.rsplit("\\", 1)[-1]
    if "@" in login:
        login = login.split("@", 1)[0]
    return login


def _extra_admin_users():
    valor = getattr(settings, "EXTRA_ADMIN_USERS", "")
    if isinstance(valor, (list, tuple, set)):
        bruto = valor
    else:
        bruto = str(valor or "").split(",")
    return {
        _normalizar_login_usuario(item)
        for item in bruto
        if _normalizar_login_usuario(item)
    }


def usuario_eh_admin_extra(user):
    if not getattr(user, "is_authenticated", False):
        return False
    candidatos = {
        _normalizar_login_usuario(getattr(user, "username", "")),
        _normalizar_login_usuario(user.get_username() if hasattr(user, "get_username") else ""),
    }
    extras = _extra_admin_users()
    concedido = bool(extras.intersection(candidatos))
    if concedido:
        login = next(iter(extras.intersection(candidatos)))
        if login not in _EXTRA_ADMIN_LOGADOS:
            logger.info(
                "Permissao administrativa concedida por EXTRA_ADMIN_USERS para usuario=%s id=%s.",
                login,
                getattr(user, "pk", None),
            )
            _EXTRA_ADMIN_LOGADOS.add(login)
    return concedido


def usuario_tem_grupo(user, nome_grupo):
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            usuario_eh_superadmin(user)
            or user.groups.filter(name=nome_grupo).exists()
        )
    )


def usuario_eh_superadmin(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def usuario_eh_domain_admin(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            usuario_eh_admin_extra(user)
            or user.groups.filter(name=GRUPO_DOMAIN_ADMINS).exists()
        )
    )


def usuario_tem_acesso_total(user):
    return usuario_eh_superadmin(user) or usuario_eh_domain_admin(user) or usuario_eh_admin_extra(user)


def _usuario_tem_algum_grupo(user, grupos):
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            usuario_tem_acesso_total(user)
            or user.groups.filter(name__in=grupos).exists()
        )
    )


def usuario_eh_financeiro(user):
    return usuario_tem_grupo(user, GRUPO_FINANCEIRO)


def usuario_eh_tecnico(user):
    return usuario_tem_grupo(user, GRUPO_TECNICO)


def usuario_eh_gestor(user):
    return usuario_tem_grupo(user, GRUPO_GESTOR)


def usuario_eh_admin_erp(user):
    return usuario_tem_grupo(user, GRUPO_ADMIN_ERP)


def usuario_eh_administrativo(user):
    return usuario_tem_acesso_total(user) or _usuario_tem_algum_grupo(
        user, [GRUPO_FINANCEIRO, GRUPO_GESTOR, GRUPO_ADMIN_ERP]
    )


def usuario_pode_acessar_manutencao(user):
    return usuario_tem_acesso_total(user) or usuario_eh_admin_erp(user)


def usuario_pode_acessar_erp(user):
    return _usuario_tem_algum_grupo(user, GRUPOS_ERP)


def usuario_pode_atuar_como_financeiro(user):
    return usuario_eh_administrativo(user)


def usuario_pode_gerenciar_cadastros(user):
    return _usuario_tem_algum_grupo(user, [GRUPO_GESTOR, GRUPO_ADMIN_ERP])


def usuario_pode_editar_relatorio_em_conferencia(user):
    return usuario_eh_financeiro(user) or usuario_tem_acesso_total(user)


def _usuario_log_context(user):
    return {
        "id": getattr(user, "pk", None),
        "username": getattr(user, "username", ""),
        "email": getattr(user, "email", ""),
        "autenticado": bool(getattr(user, "is_authenticated", False)),
        "superuser": bool(getattr(user, "is_superuser", False)),
    }


def _tecnico_log_context(tecnico):
    if not tecnico:
        return None
    return {
        "id": getattr(tecnico, "pk", None),
        "nome": getattr(tecnico, "nome", ""),
        "email": getattr(tecnico, "email", ""),
    }


def _relatorio_autorizacao_log_context(relatorio):
    criado_por = getattr(relatorio, "criado_por", None)
    return {
        "id": getattr(relatorio, "pk", None),
        "numero": getattr(relatorio, "numero", None),
        "status": getattr(relatorio, "status", None),
        "criado_por": _usuario_log_context(criado_por) if criado_por else {
            "id": getattr(relatorio, "criado_por_id", None),
            "username": None,
            "email": None,
            "autenticado": None,
            "superuser": None,
        },
        "tecnico_responsavel": _tecnico_log_context(
            getattr(relatorio, "tecnico_responsavel", None)
        ),
        "tecnico_reembolso": _tecnico_log_context(
            getattr(relatorio, "tecnico_reembolso", None)
        ),
    }


def _log_autorizacao_relatorio(acao, user, relatorio, validacoes, resultado):
    marcador = {
        "enviar_relatorio": "ENVIO_RELATORIO_DEBUG",
        "editar_relatorio": "EDITAR_RELATORIO_DEBUG",
        "dono_relatorio": "DONO_RELATORIO_DEBUG",
        "responsavel_relatorio": "RESPONSAVEL_RELATORIO_DEBUG",
    }.get(acao, "AUTORIZACAO_RELATORIO_DEBUG")
    contexto = {
        "acao": acao,
        "usuario_logado": _usuario_log_context(user),
        "relatorio": _relatorio_autorizacao_log_context(relatorio),
        "usuario": getattr(user, "username", ""),
        "username": getattr(user, "username", ""),
        "email": getattr(user, "email", ""),
        "user_id": getattr(user, "pk", None),
        "relatorio_id": getattr(relatorio, "pk", None),
        "status": getattr(relatorio, "status", None),
        "criado_por_id": getattr(relatorio, "criado_por_id", None),
        "criado_por_username": getattr(getattr(relatorio, "criado_por", None), "username", None),
        "tecnico_responsavel": getattr(
            getattr(relatorio, "tecnico_responsavel", None), "nome", None
        ),
        "tecnico_responsavel_email": getattr(
            getattr(relatorio, "tecnico_responsavel", None), "email", None
        ),
        "validacoes": validacoes,
        "resultado": resultado,
        "condicoes_false": [
            nome for nome, valor in validacoes.items() if valor is False
        ],
    }
    mensagem = "%s resultado=%s contexto=%s" % (
        marcador,
        resultado,
        contexto,
    )
    if resultado:
        logger.info(mensagem)
    else:
        logger.warning(mensagem)


def usuario_pode_editar_relatorio(user, relatorio):
    acesso_total = usuario_tem_acesso_total(user)
    administrativo = usuario_eh_administrativo(user)
    financeiro_ou_socio = usuario_pode_editar_relatorio_em_conferencia(user)
    status_finalizado = relatorio.status in {
        StatusRelatorio.APROVADO,
        StatusRelatorio.REJEITADO,
    }
    status_em_conferencia = relatorio.status == StatusRelatorio.CONFERENCIA
    status_editavel_por_tecnico = relatorio.status in {
        StatusRelatorio.RASCUNHO,
        StatusRelatorio.AJUSTE,
    }
    eh_dono = usuario_eh_dono_relatorio(user, relatorio)
    eh_responsavel = usuario_eh_responsavel_relatorio(user, relatorio)
    validacoes = {
        "usuario_tem_acesso_total": acesso_total,
        "usuario_eh_administrativo": administrativo,
        "usuario_financeiro_ou_socio": financeiro_ou_socio,
        "status_nao_finalizado": not status_finalizado,
        "status_em_conferencia": status_em_conferencia,
        "status_editavel_por_tecnico": status_editavel_por_tecnico,
        "usuario_eh_dono_relatorio": eh_dono,
        "usuario_eh_responsavel_relatorio": eh_responsavel,
    }
    if status_finalizado:
        _log_autorizacao_relatorio("editar_relatorio", user, relatorio, validacoes, False)
        return False
    if status_em_conferencia:
        resultado = financeiro_ou_socio
    else:
        resultado = bool(
            status_editavel_por_tecnico
            and (acesso_total or administrativo or eh_dono or eh_responsavel)
        )
    _log_autorizacao_relatorio(
        "editar_relatorio",
        user,
        relatorio,
        validacoes,
        resultado,
    )
    return resultado


def usuario_eh_dono_relatorio(user, relatorio):
    autenticado = bool(getattr(user, "is_authenticated", False))
    criado_por_igual_usuario = bool(
        getattr(relatorio, "criado_por_id", None) == getattr(user, "pk", None)
    )
    resultado = bool(autenticado and criado_por_igual_usuario)
    _log_autorizacao_relatorio(
        "dono_relatorio",
        user,
        relatorio,
        {
            "usuario_autenticado": autenticado,
            "criado_por_id_igual_usuario_id": criado_por_igual_usuario,
        },
        resultado,
    )
    return resultado


def usuario_eh_responsavel_relatorio(user, relatorio):
    autenticado = bool(getattr(user, "is_authenticated", False))
    tecnico = getattr(relatorio, "tecnico_responsavel", None)
    email_usuario = (getattr(user, "email", "") or "").strip().lower()
    email_tecnico = (getattr(tecnico, "email", "") or "").strip().lower()
    emails_iguais = bool(email_usuario and email_tecnico and email_usuario == email_tecnico)
    resultado = bool(autenticado and emails_iguais)
    _log_autorizacao_relatorio(
        "responsavel_relatorio",
        user,
        relatorio,
        {
            "usuario_autenticado": autenticado,
            "tecnico_responsavel_existe": bool(tecnico),
            "email_usuario_preenchido": bool(email_usuario),
            "email_tecnico_responsavel_preenchido": bool(email_tecnico),
            "email_usuario_igual_email_tecnico_responsavel": emails_iguais,
        },
        resultado,
    )
    return resultado


def usuario_pode_visualizar_relatorio(user, relatorio):
    if usuario_tem_acesso_total(user) or usuario_eh_administrativo(user):
        return True
    return usuario_eh_dono_relatorio(user, relatorio)


def usuario_pode_enviar_relatorio(user, relatorio):
    acesso_total = usuario_tem_acesso_total(user)
    administrativo = usuario_eh_administrativo(user)
    status_permitido = relatorio.status in {
        StatusRelatorio.RASCUNHO,
        StatusRelatorio.AJUSTE,
    }
    status_finalizado = relatorio.status in {
        StatusRelatorio.APROVADO,
        StatusRelatorio.REJEITADO,
    }
    eh_dono = usuario_eh_dono_relatorio(user, relatorio)
    eh_responsavel = usuario_eh_responsavel_relatorio(user, relatorio)
    pode_editar = status_permitido and (
        acesso_total or administrativo or eh_dono or eh_responsavel
    )
    validacoes = {
        "usuario_tem_acesso_total": acesso_total,
        "usuario_eh_administrativo": administrativo,
        "status_permitido_para_envio": status_permitido,
        "usuario_eh_dono_relatorio": eh_dono,
        "usuario_eh_responsavel_relatorio": eh_responsavel,
        "usuario_pode_editar_relatorio": pode_editar,
    }
    if not status_permitido:
        resultado = False
    else:
        resultado = pode_editar
    _log_autorizacao_relatorio(
        "enviar_relatorio",
        user,
        relatorio,
        validacoes,
        resultado,
    )
    return resultado


def queryset_relatorios_visiveis(user, queryset):
    if usuario_tem_acesso_total(user) or usuario_eh_administrativo(user):
        return queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(criado_por=user)


def permissoes_usuario(user):
    administrativo = usuario_eh_administrativo(user)
    tecnico = usuario_eh_tecnico(user)
    admin_erp = usuario_eh_admin_erp(user)
    superadmin = usuario_eh_superadmin(user)
    domain_admin = usuario_eh_domain_admin(user)
    acesso_total = usuario_tem_acesso_total(user)
    return {
        "acessa_erp": usuario_pode_acessar_erp(user),
        "administrativo": administrativo or acesso_total,
        "financeiro": usuario_pode_atuar_como_financeiro(user),
        "tecnico": tecnico,
        "gestor": usuario_eh_gestor(user),
        "admin_erp": admin_erp,
        "superadmin": superadmin,
        "domain_admin": domain_admin,
        "dashboard_global": administrativo or acesso_total,
        "dashboard_individual": not (administrativo or acesso_total),
        "visualiza_dados_globais": administrativo or acesso_total,
        "visualiza_cadastros": administrativo or acesso_total,
        "visualiza_adiantamentos": administrativo or acesso_total,
        "aprova_relatorios": administrativo or acesso_total,
        "manutencao": usuario_pode_acessar_manutencao(user),
    }


def exigir_acesso_erp(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_pode_acessar_erp(request.user):
            messages.error(request, "Seu usuário não possui perfil de acesso ao ERP.")
            logger.warning("Acesso ERP negado para usuario id=%s.", getattr(request.user, "pk", None))
            raise PermissionDenied("Usuário sem grupo ERP.")
        return view_func(request, *args, **kwargs)

    return wrapper


def exigir_administrativo(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_eh_administrativo(request.user):
            messages.error(request, "Você não tem permissão para acessar esta área.")
            logger.warning("Acesso administrativo negado para usuario id=%s.", getattr(request.user, "pk", None))
            return redirect("relatorios:dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper


def exigir_financeiro(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_pode_atuar_como_financeiro(request.user):
            messages.error(request, "Você não tem permissão para executar esta ação financeira.")
            logger.warning("Acao financeira negada para usuario id=%s.", getattr(request.user, "pk", None))
            pk = kwargs.get("pk")
            if pk:
                return redirect("relatorios:relatorio_detail", pk=pk)
            return redirect("relatorios:dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper
