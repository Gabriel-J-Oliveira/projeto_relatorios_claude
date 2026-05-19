from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from relatorios.services.autorizacao_service import GRUPOS_ERP
from relatorios.services.identidade.auditoria_identidade_service import (
    registrar_evento_identidade,
)


@receiver(user_logged_in)
def registrar_login_usuario(sender, request, user, **kwargs):
    grupos_erp = list(
        user.groups.filter(name__in=GRUPOS_ERP).values_list("name", flat=True)
    )
    request.session["identidade_login_em"] = timezone.now().timestamp()
    request.session["identidade_revalidada_em"] = timezone.now().timestamp()
    request.session["identidade_grupos_erp"] = grupos_erp
    registrar_evento_identidade(
        "login_sessao_iniciada",
        user,
        {"username": user.username, "grupos_erp": grupos_erp},
    )


@receiver(user_logged_out)
def registrar_logout_usuario(sender, request, user, **kwargs):
    if user is None:
        return
    registrar_evento_identidade(
        "login_sessao_encerrada",
        user,
        {"username": user.username},
    )
