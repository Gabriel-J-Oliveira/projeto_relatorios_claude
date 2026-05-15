from functools import wraps

from django.contrib import messages
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
        and user.groups.filter(name=nome_grupo).exists()
    )


def usuario_eh_financeiro(user):
    return usuario_tem_grupo(user, GRUPO_FINANCEIRO)


def usuario_eh_tecnico(user):
    return usuario_tem_grupo(user, GRUPO_TECNICO)


def usuario_eh_gestor(user):
    return usuario_tem_grupo(user, GRUPO_GESTOR)


def usuario_eh_admin_erp(user):
    return usuario_tem_grupo(user, GRUPO_ADMIN_ERP)


def usuario_pode_acessar_erp(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and user.groups.filter(name__in=GRUPOS_ERP).exists()
    )


def usuario_pode_atuar_como_financeiro(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and user.groups.filter(
            name__in=[GRUPO_FINANCEIRO, GRUPO_GESTOR, GRUPO_ADMIN_ERP]
        ).exists()
    )


def usuario_pode_gerenciar_cadastros(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and user.groups.filter(name__in=[GRUPO_GESTOR, GRUPO_ADMIN_ERP]).exists()
    )


def usuario_pode_editar_relatorio(user, relatorio):
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return False
    if (
        relatorio.status == StatusRelatorio.AJUSTE
        and usuario_pode_atuar_como_financeiro(user)
    ):
        return False
    return getattr(user, "is_authenticated", False)


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
