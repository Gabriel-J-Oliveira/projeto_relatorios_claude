import logging


logger = logging.getLogger(__name__)


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
        dados or {},
    )

