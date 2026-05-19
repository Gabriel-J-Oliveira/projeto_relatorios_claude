import logging
from dataclasses import replace

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from relatorios.services.autorizacao_service import (
    usuario_eh_superadmin,
    usuario_pode_acessar_erp,
)
from relatorios.services.identidade.auditoria_identidade_service import (
    registrar_evento_identidade,
)
from relatorios.services.identidade.grupo_mapping_service import (
    mapear_grupos_ad_para_django,
)
from relatorios.services.identidade.ldap_directory_service import (
    buscar_snapshot_usuario_ad,
)
from relatorios.services.identidade.sincronizacao_service import (
    UsuarioExternoSnapshot,
    sincronizar_usuario_externo,
)


logger = logging.getLogger(__name__)


CHAVE_REVALIDACAO = "identidade_revalidada_em"


class IdentidadeCorporativaMiddleware:
    """
    Protecao operacional de sessao.

    O login continua sendo responsabilidade dos backends. Aqui garantimos que
    uma sessao autenticada nao siga navegando sem usuario ativo e sem perfil ERP.
    Para usuarios LDAP, a consulta ao AD e feita em intervalo controlado para
    reduzir carga e ainda refletir bloqueios/remocoes de grupo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            self._validar_usuario_autenticado(request, user)
        return self.get_response(request)

    def _validar_usuario_autenticado(self, request, user):
        if usuario_eh_superadmin(user):
            return

        if not user.is_active:
            self._encerrar_sessao(request, "usuario_inativo")

        if self._deve_revalidar_ldap(request, user):
            self._revalidar_usuario_ldap(request, user)

        if not usuario_pode_acessar_erp(user):
            self._encerrar_sessao(request, "usuario_sem_grupo_erp")

    def _deve_revalidar_ldap(self, request, user):
        if not getattr(settings, "LDAP_AUTH_ENABLED", False):
            return False
        if user.has_usable_password():
            return False

        intervalo = getattr(settings, "LDAP_SESSION_REVALIDATE_SECONDS", 300)
        if intervalo <= 0:
            return False

        ultima = request.session.get(CHAVE_REVALIDACAO)
        if not ultima:
            return True
        return (timezone.now().timestamp() - float(ultima)) >= intervalo

    def _revalidar_usuario_ldap(self, request, user):
        request.session[CHAVE_REVALIDACAO] = timezone.now().timestamp()
        resultado = buscar_snapshot_usuario_ad(user.username)

        if resultado.indisponivel:
            logger.warning(
                "AD indisponivel ao revalidar sessao de %s; sessao preservada ate o timeout.",
                user.username,
            )
            registrar_evento_identidade(
                "ldap_revalidacao_indisponivel",
                user,
                {"username": user.username, "erro": resultado.erro},
            )
            return

        if resultado.status == "nao_encontrado":
            snapshot = UsuarioExternoSnapshot(
                username=user.username,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                is_active=False,
                grupos_ad=(),
            )
            sincronizar_usuario_externo(
                snapshot,
                criar_usuario=False,
                atualizar_dados=True,
                atualizar_grupos=True,
                marcar_senha_inutilizavel=True,
            )
            self._encerrar_sessao(request, "usuario_nao_encontrado_ad")

        snapshot = resultado.snapshot
        if not snapshot:
            return

        grupos_django = mapear_grupos_ad_para_django(snapshot.grupos_ad)
        bloquear_sem_grupo = getattr(settings, "LDAP_BLOCK_USERS_WITHOUT_ERP_GROUP", True)
        if not snapshot.is_active or (bloquear_sem_grupo and not grupos_django):
            snapshot = replace(snapshot, is_active=False, grupos_ad=())

        sincronizar_usuario_externo(
            snapshot,
            criar_usuario=False,
            atualizar_dados=True,
            atualizar_grupos=True,
            marcar_senha_inutilizavel=True,
        )
        user.refresh_from_db()

        if not user.is_active:
            self._encerrar_sessao(request, "usuario_bloqueado_ad")

    def _encerrar_sessao(self, request, motivo):
        username = getattr(request.user, "username", "")
        registrar_evento_identidade(
            "sessao_bloqueada",
            request.user,
            {"username": username, "motivo": motivo},
        )
        logout(request)
        raise PermissionDenied("Usuario sem autorizacao ativa para acessar o ERP.")
