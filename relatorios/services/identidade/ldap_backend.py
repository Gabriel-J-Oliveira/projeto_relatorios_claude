import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings

from relatorios.services.identidade.auditoria_identidade_service import (
    registrar_evento_identidade,
)
from relatorios.services.identidade.grupo_mapping_service import (
    mapear_grupos_ad_para_django,
)
from relatorios.services.identidade.ldap_utils import (
    construir_snapshot_ldap,
    extrair_grupos_ad,
    normalizar_username_ad,
    status_conta_ad,
)
from relatorios.services.identidade.sincronizacao_service import (
    sincronizar_usuario_externo,
)


logger = logging.getLogger(__name__)


def _ldap_exception_classes():
    try:
        import ldap
    except ImportError:
        return None
    return ldap


def _ldap_server_uris():
    return list(getattr(settings, "LDAP_SERVER_URIS", None) or [settings.AUTH_LDAP_SERVER_URI])


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

        username_ldap = (
            normalizar_username_ad(username)
            if getattr(settings, "LDAP_NORMALIZE_USERNAME", True)
            else username
        )
        user = self._autenticar_em_dcs(request, username, username_ldap, password, **kwargs)

        if user is None:
            logger.info("Autenticacao LDAP recusada para usuario %s.", username)
            registrar_evento_identidade(
                "ldap_login_falha",
                dados={"username": username_ldap, "motivo": "recusado"},
            )
            return None

        try:
            sincronizado = self._sincronizar_usuario_ldap(user, username)
            if not sincronizado:
                return None
            user.refresh_from_db()
            logger.info("Autenticacao LDAP concluida para usuario %s.", user.username)
            registrar_evento_identidade(
                "ldap_login_sucesso",
                user,
                {"username": user.username},
            )
        except Exception:
            logger.exception("Falha ao sincronizar usuario LDAP %s.", username)
            registrar_evento_identidade(
                "ldap_sincronizacao_falha",
                dados={"username": username_ldap},
            )
            return None

        return user

    def _autenticar_em_dcs(self, request, username_original, username_ldap, password, **kwargs):
        try:
            import django_auth_ldap  # noqa: F401
        except ImportError:
            logger.exception("django-auth-ldap/python-ldap nao esta instalado. Login LDAP ignorado.")
            return None

        ultimo_erro = None
        for uri in _ldap_server_uris():
            try:
                backend = self._criar_backend_ldap()
                logger.info("Tentando autenticacao LDAP de %s em %s.", username_ldap, uri)
                with override_settings(AUTH_LDAP_SERVER_URI=uri):
                    user = backend.authenticate(
                        request,
                        username=username_ldap,
                        password=password,
                        **kwargs,
                    )
                if user is not None:
                    logger.info("Bind/autenticacao LDAP OK para %s em %s.", username_ldap, uri)
                    return user
            except Exception as exc:
                ultimo_erro = exc
                if self._erro_permite_failover(exc):
                    self._registrar_falha_dc(username_original, uri, exc)
                    continue
                self._registrar_falha_dc(username_original, uri, exc)
                return None

        if ultimo_erro is not None:
            logger.warning("Todos os DCs LDAP falharam para usuario %s.", username_original)
        return None

    def _erro_permite_failover(self, exc):
        ldap = _ldap_exception_classes()
        if not ldap:
            return False
        return isinstance(exc, (ldap.TIMEOUT, ldap.SERVER_DOWN, ldap.CONNECT_ERROR))

    def _registrar_falha_dc(self, username, uri, exc):
        ldap = _ldap_exception_classes()
        if ldap and isinstance(exc, getattr(ldap, "TIMEOUT", ())):
            logger.warning("Timeout LDAP em %s ao autenticar usuario %s.", uri, username)
            evento = "ldap_timeout"
        elif ldap and isinstance(exc, getattr(ldap, "SERVER_DOWN", ())):
            logger.warning("DC LDAP indisponivel em %s ao autenticar usuario %s.", uri, username)
            evento = "ldap_dc_indisponivel"
        elif ldap and isinstance(exc, getattr(ldap, "INVALID_CREDENTIALS", ())):
            logger.info("Credenciais LDAP recusadas para usuario %s.", username)
            evento = "ldap_credenciais_invalidas"
        else:
            logger.exception("Falha LDAP em %s ao autenticar usuario %s.", uri, username)
            evento = "ldap_falha_autenticacao"
        registrar_evento_identidade(
            evento,
            dados={"username": username, "dc": uri, "erro": exc.__class__.__name__},
        )

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
        grupos_django = mapear_grupos_ad_para_django(grupos_ad)
        snapshot = construir_snapshot_ldap(
            username_digitado,
            attrs,
            grupos_ad=grupos_ad,
        )
        if not snapshot.is_active:
            status = status_conta_ad(attrs)
            logger.warning(
                "Usuario LDAP %s bloqueado para login por status AD: %s.",
                snapshot.username,
                ", ".join(status) if status else "inativo",
            )
            registrar_evento_identidade(
                "ldap_usuario_inativo",
                user,
                {"username": snapshot.username, "status": status},
            )
            return False
        if not grupos_ad:
            logger.warning("Usuario LDAP %s autenticado sem grupos AD encontrados.", snapshot.username)
        logger.info(
            "Usuario LDAP %s possui %s grupo(s) AD e %s grupo(s) ERP mapeado(s): %s.",
            snapshot.username,
            len(grupos_ad),
            len(grupos_django),
            ", ".join(grupos_django) if grupos_django else "-",
        )

        resultado = sincronizar_usuario_externo(
            snapshot,
            criar_usuario=True,
            atualizar_dados=True,
            atualizar_grupos=True,
            marcar_senha_inutilizavel=True,
        )
        registrar_evento_identidade(
            "ldap_sincronizacao_grupos",
            user,
            resultado.__dict__,
        )
        return True
