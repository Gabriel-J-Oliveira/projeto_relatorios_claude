import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from relatorios.models import Tecnico


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsuarioAD:
    username: str
    nome: str
    email: str = ""
    distinguished_name: str = ""
    user_principal_name: str = ""
    ativo: bool = True


@dataclass(frozen=True)
class ResultadoSyncUsuariosAD:
    encontrados: int = 0
    criados: int = 0
    atualizados: int = 0
    sem_alteracao: int = 0
    ignorados: int = 0
    erros: int = 0
    dry_run: bool = True


def _decode_attr(valor):
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="ignore").strip()
    return str(valor or "").strip()


def _first_attr(attrs, *nomes):
    for nome in nomes:
        valores = attrs.get(nome) or []
        if valores:
            return _decode_attr(valores[0])
    return ""


def _ldap_uris():
    uris = list(getattr(settings, "LDAP_SERVER_URIS", []) or [])
    if not uris and getattr(settings, "LDAP_SERVER_URI", ""):
        uris = [settings.LDAP_SERVER_URI]
    return uris


def _connection_options(ldap_module):
    options = {}
    if getattr(settings, "LDAP_DISABLE_REFERRALS", True):
        options[ldap_module.OPT_REFERRALS] = 0
    options[ldap_module.OPT_NETWORK_TIMEOUT] = getattr(settings, "LDAP_NETWORK_TIMEOUT", 5)
    options[ldap_module.OPT_TIMEOUT] = getattr(settings, "LDAP_OPERATION_TIMEOUT", 10)
    return options


def buscar_usuarios_ad():
    if not getattr(settings, "AD_SYNC_ENABLED", True):
        logger.info("Sincronizacao AD desativada por AD_SYNC_ENABLED=False.")
        return []

    try:
        import ldap
    except Exception as exc:
        raise RuntimeError("Dependencias LDAP indisponiveis. Instale python-ldap/django-auth-ldap.") from exc

    base_dn = (
        getattr(settings, "AD_USERS_BASE_DN", "")
        or getattr(settings, "LDAP_USER_SEARCH_BASE_DN", "")
    )
    filtro = getattr(settings, "AD_USERS_FILTER", "") or "(objectClass=user)"
    if not base_dn:
        raise RuntimeError("Configure AD_USERS_BASE_DN ou LDAP_USER_SEARCH_BASE_DN para sincronizar usuarios AD.")

    attrs = [
        "sAMAccountName",
        "displayName",
        "cn",
        "mail",
        "distinguishedName",
        "userPrincipalName",
        "userAccountControl",
    ]
    ultimo_erro = None

    for uri in _ldap_uris():
        conn = None
        try:
            logger.info("Buscando usuarios AD em %s base=%s", uri, base_dn)
            conn = ldap.initialize(uri)
            for opcao, valor in _connection_options(ldap).items():
                conn.set_option(opcao, valor)
            if getattr(settings, "LDAP_START_TLS", False):
                conn.start_tls_s()
            conn.simple_bind_s(
                getattr(settings, "LDAP_BIND_DN", ""),
                getattr(settings, "LDAP_BIND_PASSWORD", ""),
            )
            resultados = conn.search_s(base_dn, ldap.SCOPE_SUBTREE, filtro, attrs)
            usuarios = []
            for dn, payload in resultados:
                if not dn:
                    continue
                username = _first_attr(payload, "sAMAccountName")
                nome = _first_attr(payload, "displayName", "cn") or username
                email = _first_attr(payload, "mail")
                upn = _first_attr(payload, "userPrincipalName")
                dn_attr = _first_attr(payload, "distinguishedName") or _decode_attr(dn)
                uac = _first_attr(payload, "userAccountControl")
                desativado = False
                if uac.isdigit():
                    desativado = bool(int(uac) & 2)
                if not username or desativado:
                    continue
                usuarios.append(
                    UsuarioAD(
                        username=username.strip().lower(),
                        nome=nome,
                        email=email,
                        distinguished_name=dn_attr,
                        user_principal_name=upn,
                        ativo=True,
                    )
                )
            logger.info("Usuarios AD encontrados: %s", len(usuarios))
            return usuarios
        except ldap.INVALID_CREDENTIALS as exc:
            raise RuntimeError("Credenciais LDAP de servico invalidas.") from exc
        except ldap.LDAPError as exc:
            ultimo_erro = exc
            logger.warning("Falha ao buscar usuarios AD em %s: %s", uri, exc)
        finally:
            if conn is not None:
                try:
                    conn.unbind_s()
                except Exception:
                    pass

    raise RuntimeError(f"Nenhum DC LDAP respondeu para sincronizacao AD: {ultimo_erro}")


def sincronizar_usuarios_ad(*, dry_run=True, limit=None, verbose=False):
    usuarios = buscar_usuarios_ad()
    if limit:
        usuarios = usuarios[:limit]

    resumo = {
        "encontrados": len(usuarios),
        "criados": 0,
        "atualizados": 0,
        "sem_alteracao": 0,
        "ignorados": 0,
        "erros": 0,
    }

    agora = timezone.now()
    for usuario in usuarios:
        if not usuario.email and not usuario.username:
            resumo["ignorados"] += 1
            continue

        try:
            tecnico = Tecnico.objects.filter(ad_username__iexact=usuario.username).first()
            if tecnico is None and usuario.email:
                tecnico = Tecnico.objects.filter(email__iexact=usuario.email).first()

            criado = tecnico is None
            if criado:
                tecnico = Tecnico(
                    nome=usuario.nome,
                    email=usuario.email or f"{usuario.username}@ad.local",
                )

            mudancas = {
                "nome": usuario.nome,
                "email": usuario.email or tecnico.email,
                "ad_username": usuario.username,
                "ad_user_principal_name": usuario.user_principal_name,
                "ad_distinguished_name": usuario.distinguished_name,
                "origem_ad": True,
                "ativo": usuario.ativo,
            }
            alterado = criado or any(getattr(tecnico, campo) != valor for campo, valor in mudancas.items())

            if not alterado:
                resumo["sem_alteracao"] += 1
                continue

            if dry_run:
                resumo["criados" if criado else "atualizados"] += 1
                if verbose:
                    logger.info("Dry-run AD tecnico=%s criado=%s", usuario.username, criado)
                continue

            for campo, valor in mudancas.items():
                setattr(tecnico, campo, valor)
            tecnico.ad_sincronizado_em = agora
            tecnico.save()
            resumo["criados" if criado else "atualizados"] += 1
        except Exception as exc:
            resumo["erros"] += 1
            logger.exception("Erro ao sincronizar usuario AD %s: %s", usuario.username, exc)

    return ResultadoSyncUsuariosAD(dry_run=dry_run, **resumo)
