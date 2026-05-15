import logging

from django.conf import settings
from django.contrib.auth import get_user_model

from relatorios.services.identidade.ldap_utils import (
    construir_snapshot_ldap,
    extrair_grupos_ad,
    normalizar_username_ad,
)
from relatorios.services.identidade.sincronizacao_service import (
    sincronizar_usuario_externo,
)


logger = logging.getLogger(__name__)


class ActiveDirectoryBackend:
    """
    Backend incremental para AD.

    Quando LDAP_AUTH_ENABLED=False, não importa django-auth-ldap e deixa o
    ModelBackend autenticar usuários locais normalmente.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not getattr(settings, "LDAP_AUTH_ENABLED", False):
            return None

        if not username or not password:
            return None

        try:
            backend = self._criar_backend_ldap()
            username_ldap = (
                normalizar_username_ad(username)
                if getattr(settings, "LDAP_NORMALIZE_USERNAME", True)
                else username
            )
            user = backend.authenticate(
                request,
                username=username_ldap,
                password=password,
                **kwargs,
            )
        except ImportError:
            logger.exception(
                "django-auth-ldap/python-ldap nao esta instalado. Login LDAP ignorado."
            )
            return None
        except Exception:
            logger.exception("Falha durante autenticacao LDAP para usuario %s.", username)
            return None

        if user is None:
            logger.info("Autenticacao LDAP recusada para usuario %s.", username)
            return None

        try:
            self._sincronizar_usuario_ldap(user, username)
            user.refresh_from_db()
            logger.info("Autenticacao LDAP concluida para usuario %s.", user.username)
        except Exception:
            logger.exception("Falha ao sincronizar usuario LDAP %s.", username)
            return None

        return user

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def _criar_backend_ldap(self):
        from django_auth_ldap.backend import LDAPBackend

        class ERPLDAPBackend(LDAPBackend):
            def ldap_to_django_username(self, username):
                return normalizar_username_ad(username)

        return ERPLDAPBackend()

    def _sincronizar_usuario_ldap(self, user, username_digitado):
        ldap_user = getattr(user, "ldap_user", None)
        attrs = getattr(ldap_user, "attrs", {}) or {}
        grupos_ad = extrair_grupos_ad(ldap_user=ldap_user, attrs=attrs)
        snapshot = construir_snapshot_ldap(
            username_digitado,
            attrs,
            grupos_ad=grupos_ad,
        )

        sincronizar_usuario_externo(
            snapshot,
            criar_usuario=True,
            atualizar_dados=True,
            atualizar_grupos=True,
            marcar_senha_inutilizavel=True,
        )
