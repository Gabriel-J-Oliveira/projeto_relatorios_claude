import logging

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


class ClientesApiError(Exception):
    """Erro controlado na integracao de clientes."""


class ClientesApiConfigError(ClientesApiError):
    """Configuracao incompleta ou desabilitada."""


def buscar_clientes_api():
    """Busca clientes na API externa e retorna a lista bruta de registros."""
    if not getattr(settings, "CLIENTES_API_ENABLED", True):
        raise ClientesApiConfigError("Sincronizacao de clientes desabilitada.")

    token = getattr(settings, "CLIENTES_API_TOKEN", "")
    if not token:
        raise ClientesApiConfigError("CLIENTES_API_TOKEN nao configurado.")

    url = getattr(settings, "CLIENTES_API_URL", "")
    timeout = getattr(settings, "CLIENTES_API_TIMEOUT", 30)
    if not url:
        raise ClientesApiConfigError("CLIENTES_API_URL nao configurada.")

    logger.info("Iniciando busca de clientes na API ControlSul.")
    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except requests.Timeout as exc:
        logger.warning("Timeout ao buscar clientes na API ControlSul.")
        raise ClientesApiError("A API de clientes demorou para responder.") from exc
    except requests.RequestException as exc:
        logger.warning("Falha de conexao ao buscar clientes na API ControlSul: %s", exc)
        raise ClientesApiError("A API de clientes esta indisponivel no momento.") from exc

    if response.status_code == 304:
        logger.warning("API de clientes retornou 304 sem corpo novo.")
        return []
    if response.status_code in {401, 403}:
        logger.error("API de clientes retornou acesso negado: status=%s", response.status_code)
        raise ClientesApiError("A API recusou a credencial configurada.")
    if response.status_code >= 500:
        logger.warning("API de clientes indisponivel: status=%s", response.status_code)
        raise ClientesApiError("A API de clientes esta temporariamente indisponivel.")
    if response.status_code != 200:
        logger.warning("API de clientes retornou status inesperado: %s", response.status_code)
        raise ClientesApiError("A API de clientes retornou uma resposta inesperada.")

    try:
        payload = response.json()
    except ValueError as exc:
        logger.error("API de clientes retornou JSON invalido.")
        raise ClientesApiError("A API de clientes retornou dados invalidos.") from exc

    if isinstance(payload, list):
        clientes = payload
    elif isinstance(payload, dict):
        clientes = (
            payload.get("data")
            or payload.get("clients")
            or payload.get("results")
            or payload.get("items")
            or []
        )
    else:
        clientes = []

    if not isinstance(clientes, list):
        logger.error("API de clientes retornou formato inesperado.")
        raise ClientesApiError("A API de clientes retornou formato inesperado.")

    logger.info("API de clientes retornou %s registro(s).", len(clientes))
    return clientes
