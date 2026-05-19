import logging
from dataclasses import dataclass

from django.conf import settings

from relatorios.services.identidade.cache_service import (
    gravar_snapshot_diretorio,
    obter_snapshot_diretorio,
)
from relatorios.services.identidade.ldap_utils import (
    construir_snapshot_ldap,
    normalizar_username_ad,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoConsultaDiretorio:
    status: str
    snapshot: object = None
    erro: str = ""
    dc: str = ""

    @property
    def encontrado(self):
        return self.status == "ok" and self.snapshot is not None

    @property
    def indisponivel(self):
        return self.status == "indisponivel"


def _ldap_server_uris():
    return list(getattr(settings, "LDAP_SERVER_URIS", None) or [settings.AUTH_LDAP_SERVER_URI])


def _atributos_usuario():
    return list(
        getattr(
            settings,
            "AUTH_LDAP_USER_ATTRLIST",
            [
                "sAMAccountName",
                "userPrincipalName",
                "givenName",
                "sn",
                "displayName",
                "mail",
                "distinguishedName",
                "memberOf",
                "primaryGroupID",
                "userAccountControl",
                "lockoutTime",
                "accountExpires",
            ],
        )
    )


def _normalizar_attrs_para_cache(attrs):
    dados = {}
    for chave, valores in (attrs or {}).items():
        dados[chave] = [
            valor.decode("utf-8", errors="ignore") if isinstance(valor, bytes) else str(valor)
            for valor in valores
        ]
    return dados


def _montar_snapshot(username, attrs):
    from relatorios.services.identidade.ldap_utils import extrair_grupos_ad

    grupos_ad = extrair_grupos_ad(attrs=attrs)
    return construir_snapshot_ldap(username, attrs, grupos_ad=grupos_ad)


def buscar_snapshot_usuario_ad(username, *, usar_cache=True):
    """
    Busca dados atuais do usuario no AD com bind de servico.

    Usado para revalidacao periodica de sessao. Nao valida senha e nao substitui
    o login LDAP; apenas confirma status e grupos sem tocar em views.
    """
    if not getattr(settings, "LDAP_AUTH_ENABLED", False):
        return ResultadoConsultaDiretorio(status="desabilitado")

    username_ldap = normalizar_username_ad(username)
    if usar_cache:
        dados_cache = obter_snapshot_diretorio(username_ldap)
        if dados_cache:
            snapshot = _montar_snapshot(username_ldap, dados_cache.get("attrs") or {})
            return ResultadoConsultaDiretorio(
                status=dados_cache.get("status", "ok"),
                snapshot=snapshot,
                dc=dados_cache.get("dc", ""),
            )

    try:
        import ldap
        from ldap.filter import escape_filter_chars
    except ImportError:
        logger.warning("python-ldap nao instalado; consulta de diretorio ignorada.")
        return ResultadoConsultaDiretorio(status="indisponivel", erro="python_ldap_ausente")

    filtro = getattr(settings, "LDAP_USER_SEARCH_FILTER", "(sAMAccountName=%(user)s)") % {
        "user": escape_filter_chars(username_ldap)
    }
    ultimo_erro = ""

    for uri in _ldap_server_uris():
        try:
            for opcao, valor in getattr(settings, "AUTH_LDAP_GLOBAL_OPTIONS", {}).items():
                ldap.set_option(opcao, valor)

            conexao = ldap.initialize(uri)
            for opcao, valor in getattr(settings, "AUTH_LDAP_CONNECTION_OPTIONS", {}).items():
                conexao.set_option(opcao, valor)
            if getattr(settings, "AUTH_LDAP_START_TLS", False):
                conexao.start_tls_s()
            conexao.simple_bind_s(
                getattr(settings, "AUTH_LDAP_BIND_DN", ""),
                getattr(settings, "AUTH_LDAP_BIND_PASSWORD", ""),
            )
            resultados = conexao.search_s(
                getattr(settings, "LDAP_USER_SEARCH_BASE_DN", ""),
                ldap.SCOPE_SUBTREE,
                filtro,
                _atributos_usuario(),
            )
            for dn, attrs in resultados:
                if dn and isinstance(attrs, dict):
                    snapshot = _montar_snapshot(username_ldap, attrs)
                    gravar_snapshot_diretorio(
                        username_ldap,
                        {
                            "status": "ok",
                            "dc": uri,
                            "attrs": _normalizar_attrs_para_cache(attrs),
                        },
                    )
                    logger.info("Consulta AD de sessao OK para %s em %s.", username_ldap, uri)
                    return ResultadoConsultaDiretorio(status="ok", snapshot=snapshot, dc=uri)

            logger.info("Usuario %s nao encontrado no AD em %s.", username_ldap, uri)
            return ResultadoConsultaDiretorio(status="nao_encontrado", dc=uri)
        except (ldap.TIMEOUT, ldap.SERVER_DOWN, ldap.CONNECT_ERROR) as exc:
            ultimo_erro = exc.__class__.__name__
            logger.warning(
                "DC LDAP indisponivel na consulta de sessao de %s em %s: %s.",
                username_ldap,
                uri,
                ultimo_erro,
            )
            continue
        except ldap.INVALID_CREDENTIALS:
            logger.error("Credenciais do bind de servico LDAP invalidas na consulta de sessao.")
            return ResultadoConsultaDiretorio(status="indisponivel", erro="bind_servico_invalido")
        except ldap.LDAPError as exc:
            ultimo_erro = exc.__class__.__name__
            logger.warning(
                "Falha LDAP na consulta de sessao de %s em %s: %s.",
                username_ldap,
                uri,
                ultimo_erro,
            )
            continue

    return ResultadoConsultaDiretorio(status="indisponivel", erro=ultimo_erro)
