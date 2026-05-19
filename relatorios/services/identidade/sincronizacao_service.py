import logging
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from relatorios.services.autorizacao_service import GRUPOS_ERP
from relatorios.services.identidade.auditoria_identidade_service import (
    registrar_evento_identidade,
)
from relatorios.services.identidade.grupo_mapping_service import (
    garantir_grupos_erp,
    mapear_grupos_ad_para_django,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsuarioExternoSnapshot:
    """
    Representa dados já obtidos de uma fonte externa.

    Esta classe não sabe buscar no LDAP; ela apenas padroniza a entrada da
    sincronização para manter autenticação e autorização desacopladas.
    """

    username: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    is_active: bool = True
    grupos_ad: tuple[str, ...] = field(default_factory=tuple)
    identificador_externo: str = ""


@dataclass(frozen=True)
class ResultadoSincronizacaoUsuario:
    username: str
    criado: bool
    atualizado: bool
    dry_run: bool
    grupos_django: tuple[str, ...]
    grupos_adicionados: tuple[str, ...]
    grupos_removidos: tuple[str, ...]
    usuario_local_migrado: bool = False


def _normalizar_snapshot(snapshot):
    username = (snapshot.username or "").strip()
    if not username:
        raise ValueError("Snapshot externo sem username.")
    return UsuarioExternoSnapshot(
        username=username,
        email=(snapshot.email or "").strip(),
        first_name=(snapshot.first_name or "").strip(),
        last_name=(snapshot.last_name or "").strip(),
        is_active=bool(snapshot.is_active),
        grupos_ad=tuple(snapshot.grupos_ad or ()),
        identificador_externo=(snapshot.identificador_externo or "").strip(),
    )


def sincronizar_usuario_externo(
    snapshot,
    *,
    criar_usuario=True,
    atualizar_dados=True,
    atualizar_grupos=True,
    marcar_senha_inutilizavel=False,
    dry_run=False,
    mapeamento_grupos=None,
):
    """
    Sincroniza um usuário local a partir de dados externos já resolvidos.

    Não autentica no AD, não valida senha e não conecta em LDAP. O objetivo é
    preparar a camada operacional que um backend LDAP poderá chamar depois.
    """
    snapshot = _normalizar_snapshot(snapshot)
    grupos_django = tuple(
        mapear_grupos_ad_para_django(snapshot.grupos_ad, mapeamento_grupos)
    )

    User = get_user_model()
    usuario = User.objects.filter(username=snapshot.username).first()
    criado = usuario is None
    usuario_local_migrado = bool(
        usuario and marcar_senha_inutilizavel and usuario.has_usable_password()
    )

    if criado and not criar_usuario:
        raise User.DoesNotExist(f"Usuario {snapshot.username} nao existe.")

    grupos_erp_atuais = set()
    if usuario:
        grupos_erp_atuais = set(
            usuario.groups.filter(name__in=GRUPOS_ERP).values_list("name", flat=True)
        )

    grupos_destino = set(grupos_django)
    grupos_adicionados = tuple(sorted(grupos_destino - grupos_erp_atuais))
    grupos_removidos = tuple(sorted(grupos_erp_atuais - grupos_destino))
    atualizado = bool(
        criado
        or grupos_adicionados
        or grupos_removidos
        or (
            usuario
            and atualizar_dados
            and (
                usuario.email != snapshot.email
                or usuario.first_name != snapshot.first_name
                or usuario.last_name != snapshot.last_name
                or usuario.is_active != snapshot.is_active
            )
        )
    )

    resultado = ResultadoSincronizacaoUsuario(
        username=snapshot.username,
        criado=criado,
        atualizado=atualizado,
        dry_run=dry_run,
        grupos_django=grupos_django,
        grupos_adicionados=grupos_adicionados,
        grupos_removidos=grupos_removidos,
        usuario_local_migrado=usuario_local_migrado,
    )

    if dry_run:
        registrar_evento_identidade(
            "sincronizacao_usuario_dry_run",
            usuario,
            resultado.__dict__,
        )
        return resultado

    with transaction.atomic():
        garantir_grupos_erp()
        usuario = User.objects.select_for_update().filter(
            username=snapshot.username
        ).first()

        if usuario is None:
            usuario = User(username=snapshot.username)
            usuario.set_unusable_password()
            logger.info("Usuario Django %s criado a partir da identidade externa.", snapshot.username)
        elif marcar_senha_inutilizavel and usuario.has_usable_password():
            usuario.set_unusable_password()
            logger.warning(
                "Usuario local %s migrado para identidade externa; senha local inutilizada.",
                snapshot.username,
            )

        if atualizar_dados:
            usuario.email = snapshot.email
            usuario.first_name = snapshot.first_name
            usuario.last_name = snapshot.last_name
            usuario.is_active = snapshot.is_active

        usuario.save()
        if atualizado and not criado:
            logger.info("Usuario Django %s atualizado a partir da identidade externa.", snapshot.username)

        if atualizar_grupos:
            grupos_preservados = usuario.groups.exclude(name__in=GRUPOS_ERP)
            grupos_mapeados = Group.objects.filter(name__in=grupos_django)
            usuario.groups.set([*grupos_preservados, *grupos_mapeados])
            logger.info(
                "Grupos ERP sincronizados para %s: adicionados=%s removidos=%s atuais=%s",
                snapshot.username,
                grupos_adicionados,
                grupos_removidos,
                grupos_django,
            )

        registrar_evento_identidade(
            "sincronizacao_usuario",
            usuario,
            resultado.__dict__,
        )

    return resultado
