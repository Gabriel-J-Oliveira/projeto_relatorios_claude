import logging
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)

CENTAVO_KM = Decimal("0.01")
DEFAULT_TIMEOUT = 8
DEFAULT_CACHE_TTL = 60 * 60 * 24
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"


class MapsServiceError(Exception):
    """Erro amigavel de integracao com servicos externos de mapas."""


def _config(nome, default):
    return getattr(settings, nome, default)


def _timeout():
    return int(_config("MAPS_REQUEST_TIMEOUT", DEFAULT_TIMEOUT))


def _cache_ttl():
    return int(_config("MAPS_CACHE_TTL", DEFAULT_CACHE_TTL))


def _user_agent():
    return _config(
        "MAPS_USER_AGENT",
        "ControlSulERP/1.0 (relatorios.control.local; financeiro@controlsul.local)",
    )


def _normalizar_query(query):
    return " ".join(str(query or "").strip().lower().split())


def _cache_key(prefixo, valor):
    digest = hashlib.sha256(str(valor).encode("utf-8")).hexdigest()
    return f"{prefixo}:{digest}"


def _decimal_coord(valor, nome):
    try:
        coord = Decimal(str(valor).strip())
    except (InvalidOperation, AttributeError):
        raise MapsServiceError(f"Parâmetro {nome} inválido.")
    return coord


def _distancia_km(metros):
    return (Decimal(str(metros)) / Decimal("1000")).quantize(
        CENTAVO_KM,
        rounding=ROUND_HALF_UP,
    )


def _duracao_texto(segundos):
    segundos = int(segundos or 0)
    horas, resto = divmod(segundos, 3600)
    minutos = round(resto / 60)
    if horas and minutos:
        return f"{horas}h {minutos}min"
    if horas:
        return f"{horas}h"
    return f"{minutos}min"


def buscar_endereco(query):
    """
    Busca enderecos no Nominatim/OpenStreetMap.

    Retorna lista com display_name, lat e lon.
    """
    query_normalizada = _normalizar_query(query)
    if not query_normalizada:
        raise MapsServiceError("Informe um endereço para buscar.")

    cache_key = _cache_key("geocode", query_normalizada)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    params = {
        "q": query_normalizada,
        "format": "jsonv2",
        "limit": int(_config("MAPS_GEOCODE_LIMIT", 5)),
        "countrycodes": _config("MAPS_COUNTRYCODES", "br"),
        "addressdetails": 1,
    }
    headers = {"User-Agent": _user_agent()}

    try:
        response = requests.get(
            _config("MAPS_NOMINATIM_URL", NOMINATIM_URL),
            params=params,
            headers=headers,
            timeout=_timeout(),
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        logger.warning("Timeout ao buscar endereço no Nominatim: %s", query_normalizada)
        raise MapsServiceError("Tempo esgotado ao buscar endereço. Tente novamente.") from exc
    except requests.RequestException as exc:
        logger.warning("Falha ao buscar endereço no Nominatim: %s", exc)
        raise MapsServiceError("Serviço de busca de endereço indisponível no momento.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("Resposta inválida do Nominatim para query=%s", query_normalizada)
        raise MapsServiceError("Resposta inválida do serviço de endereços.") from exc

    resultados = [
        {
            "display_name": item.get("display_name", ""),
            "lat": item.get("lat"),
            "lon": item.get("lon"),
        }
        for item in payload
        if item.get("lat") and item.get("lon")
    ]

    cache.set(cache_key, resultados, _cache_ttl())
    return resultados


def calcular_rota(origem_lat, origem_lon, destino_lat, destino_lon):
    """
    Calcula rota de carro no OSRM publico.

    Retorna distancia em metros/km, duracao e geometria polyline.
    """
    origem_lat = _decimal_coord(origem_lat, "origem_lat")
    origem_lon = _decimal_coord(origem_lon, "origem_lon")
    destino_lat = _decimal_coord(destino_lat, "destino_lat")
    destino_lon = _decimal_coord(destino_lon, "destino_lon")

    origem = f"{origem_lon},{origem_lat}"
    destino = f"{destino_lon},{destino_lat}"
    cache_key = _cache_key("route", f"{origem}:{destino}")
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{_config('MAPS_OSRM_ROUTE_URL', OSRM_ROUTE_URL)}/{quote(origem)};{quote(destino)}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "false",
        "steps": "false",
    }

    try:
        response = requests.get(url, params=params, timeout=_timeout())
        response.raise_for_status()
    except requests.Timeout as exc:
        logger.warning("Timeout ao calcular rota OSRM: %s -> %s", origem, destino)
        raise MapsServiceError("Tempo esgotado ao calcular rota. Tente novamente.") from exc
    except requests.RequestException as exc:
        logger.warning("Falha ao calcular rota OSRM: %s", exc)
        raise MapsServiceError("Serviço de cálculo de rota indisponível no momento.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("Resposta inválida do OSRM para rota %s -> %s", origem, destino)
        raise MapsServiceError("Resposta inválida do serviço de rotas.") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        logger.warning("Rota não encontrada no OSRM: %s -> %s | code=%s", origem, destino, payload.get("code"))
        raise MapsServiceError("Não foi possível encontrar uma rota para os pontos informados.")

    rota = payload["routes"][0]
    distancia_metros = Decimal(str(rota.get("distance", 0))).quantize(Decimal("0.01"))
    duracao_segundos = Decimal(str(rota.get("duration", 0))).quantize(Decimal("0.01"))
    resultado = {
        "distancia_metros": str(distancia_metros),
        "distancia_km": str(_distancia_km(distancia_metros)),
        "duracao_segundos": str(duracao_segundos),
        "duracao_texto": _duracao_texto(duracao_segundos),
        "geometria": rota.get("geometry") or {},
        "rota_geojson": rota.get("geometry") or {},
    }

    cache.set(cache_key, resultado, _cache_ttl())
    return resultado
