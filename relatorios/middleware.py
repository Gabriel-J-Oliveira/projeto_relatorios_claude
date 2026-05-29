import logging
from dataclasses import replace

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils import timezone

from relatorios.models import PerfilUsuario
from relatorios.services.setores_service import aplicar_setor_importado_para_usuario, garantir_tecnico_para_usuario
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


def perfil_usuario_completo(user):
    if not getattr(user, "is_authenticated", False):
        return True
    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=user)
    if not perfil.setor_confirmado:
        aplicar_setor_importado_para_usuario(user)
        perfil.refresh_from_db()
    garantir_tecnico_para_usuario(user)
    return bool(
        perfil.cadastro_confirmado_em
        and (user.first_name or "").strip()
        and (user.last_name or "").strip()
        and (user.email or "").strip()
        and perfil.setor_confirmado
        and perfil.setor_id
    )


class CadastroObrigatorioMiddleware:
    """
    Bloqueia acesso ao ERP ate o usuario autenticado confirmar os dados
    cadastrais obrigatorios. O controle e backend-first e nao interfere no
    fluxo LDAP; usa o User Django ja autenticado.
    """

    ROTAS_LIBERADAS = {
        "login",
        "logout",
        "relatorios:completar_cadastro",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False) and not self._rota_liberada(request):
            if not perfil_usuario_completo(user):
                destino = reverse("relatorios:completar_cadastro")
                next_url = request.get_full_path()
                if url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    destino = f"{destino}?{urlencode({'next': next_url})}"
                return redirect(destino)
        return self.get_response(request)

    def _rota_liberada(self, request):
        if request.path.startswith(settings.STATIC_URL):
            return True
        if request.path.startswith(settings.MEDIA_URL):
            return True
        if request.path.startswith(getattr(settings, "ANEXOS_URL", "/anexos/")):
            return True
        caminhos_liberados = {
            reverse("login"),
            reverse("logout"),
            reverse("relatorios:completar_cadastro"),
        }
        if request.path in caminhos_liberados:
            return True
        match = getattr(request, "resolver_match", None)
        if not match:
            return False
        nomes = {match.view_name, match.url_name}
        return bool(nomes & self.ROTAS_LIBERADAS)


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
            logger.warning("Sessao bloqueada para usuario inativo id=%s.", user.pk)
            self._encerrar_sessao(request, "usuario_inativo")

        if self._deve_revalidar_ldap(request, user):
            self._revalidar_usuario_ldap(request, user)

        if not usuario_pode_acessar_erp(user):
            logger.warning("Sessao bloqueada por ausencia de grupo ERP usuario id=%s.", user.pk)
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
        logger.warning("Sessao encerrada por controle de seguranca. usuario=%s motivo=%s", username, motivo)
        raise PermissionDenied("Usuario sem autorizacao ativa para acessar o ERP.")
