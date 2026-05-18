import logging
from copy import deepcopy


logger = logging.getLogger(__name__)


CHAVES_SENSIVEIS = {"password", "senha", "credential", "credentials", "bind_password"}


def sanitizar_dados_identidade(dados):
    dados = deepcopy(dados or {})
    if isinstance(dados, dict):
        for chave in list(dados.keys()):
            if any(sensivel in str(chave).lower() for sensivel in CHAVES_SENSIVEIS):
                dados[chave] = "***"
            elif isinstance(dados[chave], dict):
                dados[chave] = sanitizar_dados_identidade(dados[chave])
    return dados


def registrar_evento_identidade(evento, usuario=None, dados=None):
    """
    Ponto único para auditoria futura de sincronização de identidade.

    Nesta etapa não persiste em banco para evitar criar uma trilha incompleta
    antes do desenho definitivo de auditoria de AD/LDAP.
    """
    username = getattr(usuario, "username", None) or "-"
    logger.info(
        "identidade.%s usuario=%s dados=%s",
        evento,
        username,
        sanitizar_dados_identidade(dados),
    )
