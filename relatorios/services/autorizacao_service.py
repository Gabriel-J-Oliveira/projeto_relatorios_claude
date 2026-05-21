from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from relatorios.models import StatusRelatorio


GRUPO_FINANCEIRO = "Financeiro"
GRUPO_TECNICO = "Tecnico"
GRUPO_GESTOR = "Gestor"
GRUPO_ADMIN_ERP = "Administrador ERP"

GRUPOS_ERP = [
    GRUPO_FINANCEIRO,
    GRUPO_TECNICO,
    GRUPO_GESTOR,
    GRUPO_ADMIN_ERP,
]


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


def _usuario_tem_algum_grupo(user, grupos):
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            usuario_eh_superadmin(user)
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
    return _usuario_tem_algum_grupo(
        user, [GRUPO_FINANCEIRO, GRUPO_GESTOR, GRUPO_ADMIN_ERP]
    )


def usuario_pode_acessar_erp(user):
    return _usuario_tem_algum_grupo(user, GRUPOS_ERP)


def usuario_pode_atuar_como_financeiro(user):
    return usuario_eh_administrativo(user)


def usuario_pode_gerenciar_cadastros(user):
    return _usuario_tem_algum_grupo(user, [GRUPO_GESTOR, GRUPO_ADMIN_ERP])


def usuario_pode_editar_relatorio(user, relatorio):
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return False
    if usuario_eh_superadmin(user):
        return True
    if (
        relatorio.status == StatusRelatorio.AJUSTE
        and usuario_pode_atuar_como_financeiro(user)
    ):
        return False
    if usuario_eh_administrativo(user):
        return True
    return usuario_eh_dono_relatorio(user, relatorio)


def usuario_eh_dono_relatorio(user, relatorio):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(relatorio, "criado_por_id", None) == user.pk)


def usuario_pode_visualizar_relatorio(user, relatorio):
    if usuario_eh_superadmin(user) or usuario_eh_administrativo(user):
        return True
    return usuario_eh_dono_relatorio(user, relatorio)


def usuario_pode_enviar_relatorio(user, relatorio):
    if usuario_eh_superadmin(user):
        return True
    if relatorio.status not in {StatusRelatorio.RASCUNHO, StatusRelatorio.AJUSTE}:
        return False
    return usuario_pode_editar_relatorio(user, relatorio)


def queryset_relatorios_visiveis(user, queryset):
    if usuario_eh_superadmin(user) or usuario_eh_administrativo(user):
        return queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset.filter(criado_por=user)


def permissoes_usuario(user):
    administrativo = usuario_eh_administrativo(user)
    tecnico = usuario_eh_tecnico(user)
    admin_erp = usuario_eh_admin_erp(user)
    superadmin = usuario_eh_superadmin(user)
    return {
        "acessa_erp": usuario_pode_acessar_erp(user),
        "administrativo": administrativo,
        "financeiro": usuario_pode_atuar_como_financeiro(user),
        "tecnico": tecnico,
        "gestor": usuario_eh_gestor(user),
        "admin_erp": admin_erp,
        "superadmin": superadmin,
        "dashboard_global": administrativo,
        "dashboard_individual": not administrativo,
        "visualiza_dados_globais": administrativo,
        "visualiza_cadastros": administrativo,
        "visualiza_adiantamentos": administrativo,
        "aprova_relatorios": administrativo,
    }


def exigir_acesso_erp(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_pode_acessar_erp(request.user):
            messages.error(request, "Seu usuÃ¡rio nÃ£o possui perfil de acesso ao ERP.")
            raise PermissionDenied("UsuÃ¡rio sem grupo ERP.")
        return view_func(request, *args, **kwargs)

    return wrapper


def exigir_administrativo(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_eh_administrativo(request.user):
            messages.error(request, "VocÃª nÃ£o tem permissÃ£o para acessar esta Ã¡rea.")
            return redirect("relatorios:dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper


def exigir_financeiro(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not usuario_pode_atuar_como_financeiro(request.user):
            messages.error(request, "Você não tem permissão para executar esta ação financeira.")
            pk = kwargs.get("pk")
            if pk:
                return redirect("relatorios:relatorio_detail", pk=pk)
            return redirect("relatorios:dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper
