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


def usuario_pode_acessar_erp(user):
    return _usuario_tem_algum_grupo(user, GRUPOS_ERP)


def usuario_pode_atuar_como_financeiro(user):
    return usuario_eh_administrativo(user)


def usuario_pode_gerenciar_cadastros(user):
    return _usuario_tem_algum_grupo(user, [GRUPO_GESTOR, GRUPO_ADMIN_ERP])


def usuario_pode_editar_relatorio(user, relatorio):
    if usuario_tem_acesso_total(user):
        return True
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return False
    return usuario_eh_dono_relatorio(user, relatorio) or usuario_eh_responsavel_relatorio(
        user, relatorio
    )


def usuario_eh_dono_relatorio(user, relatorio):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(relatorio, "criado_por_id", None) == user.pk)


def usuario_eh_responsavel_relatorio(user, relatorio):
    if not getattr(user, "is_authenticated", False):
        return False
    tecnico = getattr(relatorio, "tecnico_responsavel", None)
    email_usuario = (getattr(user, "email", "") or "").strip().lower()
    email_tecnico = (getattr(tecnico, "email", "") or "").strip().lower()
    return bool(email_usuario and email_tecnico and email_usuario == email_tecnico)


def usuario_pode_visualizar_relatorio(user, relatorio):
    if usuario_tem_acesso_total(user) or usuario_eh_administrativo(user):
        return True
    return usuario_eh_dono_relatorio(user, relatorio)


def usuario_pode_enviar_relatorio(user, relatorio):
    if usuario_tem_acesso_total(user):
        return True
    if relatorio.status not in {StatusRelatorio.RASCUNHO, StatusRelatorio.AJUSTE}:
        return False
    return usuario_pode_editar_relatorio(user, relatorio)


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
