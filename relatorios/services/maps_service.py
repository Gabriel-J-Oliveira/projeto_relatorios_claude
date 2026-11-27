import logging
import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, quote, unquote_plus, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache, caches


logger = logging.getLogger(__name__)

CENTAVO_KM = Decimal("0.01")
DEFAULT_TIMEOUT = 8
DEFAULT_CACHE_TTL = 60 * 60 * 24
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/"
MARCO_KM = re.compile(r"\bkm\s*\.?\s*(\d+)(?!\d)", re.I)


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
    texto = unicodedata.normalize("NFKC", str(query or ""))
    texto = re.sub(r"[\u200b-\u200f\ufeff]", "", texto)
    return " ".join(texto.strip().lower().split())


def _preparar_busca(query):
    texto = str(query or "")
    if texto.strip().startswith(("https://", "http://")):
        try:
            url = urlsplit(texto.strip())
        except ValueError:
            return ""
        if url.hostname in {"google.com", "www.google.com", "maps.google.com", "www.google.com.br"}:
            partes = url.path.split("/")
            if "place" in partes and partes.index("place") + 1 < len(partes):
                texto = unquote_plus(partes[partes.index("place") + 1])
            else:
                parametros = parse_qs(url.query)
                texto = (parametros.get("query") or parametros.get("q") or [texto])[0]
    texto = re.sub(r"\b(?:n[º°]|nro\.)\s*(\d+)", r"\1", texto, flags=re.I)
    texto = unicodedata.normalize("NFKC", texto)
    texto = re.sub(r"[\u200b-\u200f\ufeff]", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ,")
    texto = re.sub(r"\b(\d{5})[\s-]?(\d{3})\b", r"\1-\2", texto)
    texto = re.sub(r"\s*[–—]\s*", " - ", texto)
    texto = re.sub(r",?\s+Brasil$", "", texto, flags=re.I)
    texto = re.sub(r"\bBR\s*-?\s*(\d{3})\b", r"BR-\1", texto, flags=re.I)
    texto = re.sub(r"\bkm\s*\.?\s*(\d+)", r"km \1", texto, flags=re.I)
    for abreviacao, nome in (("av", "Avenida"), ("r", "Rua"), ("rod", "Rodovia"), ("trav", "Travessa")):
        texto = re.sub(rf"(^|,\s*){abreviacao}\.\s*", rf"\1{nome} ", texto, flags=re.I)
    return texto


def _tentativas_busca(query, localidade):
    cep = re.search(r"\b\d{5}-\d{3}\b", query)
    partes = [parte.strip() for parte in query.split(",")]
    if len(partes) > 1 and re.fullmatch(r"\d+\w?(?:\s+-\s+.*)?", partes[1]):
        componentes = {"street": partes[0], "housenumber": partes[1].split()[0]}
        if cep:
            componentes["postcode"] = cep.group()
        cidade = re.search(r",\s*([^,]+?)\s*[-/]\s*([A-Z]{2})(?=,|$)", query)
        if cidade:
            componentes.update(city=cidade[1].strip(), state=cidade[2])
        yield componentes
    elif localidade and _normalizar_query(localidade) not in _normalizar_query(query):
        if not re.search(r"\d", query) and not query.lower().startswith(("rua ", "avenida ", "rodovia ", "travessa ")):
            local = re.split(r"\s*[-/]\s*(?=[A-Z]{2}$)", localidade, maxsplit=1)
            yield {"name": query, "city": local[0], **({"state": local[1]} if len(local) > 1 else {})}
        else:
            yield {"text": f"{query}, {localidade}"}
    if cep and cep.group() != query:
        yield {"postcode": cep.group()}


def _marcos_km(item):
    return {
        match.group(1)
        for campo in ("formatted", "display_name", "name", "housenumber", "street", "address_line1")
        for match in MARCO_KM.finditer(item.get(campo) or "")
    }


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


def _reservar_cota_geoapify():
    """Redis executa a reserva atomicamente entre processos, usando o relógio do servidor."""
    from django.core.cache.backends.redis import RedisCache

    if "geocoding" not in settings.CACHES:
        raise MapsServiceError("Busca de endereço indisponível: cache compartilhado não configurado.")
    backend = caches["geocoding"]
    if not isinstance(backend, RedisCache):
        raise MapsServiceError("Busca de endereço indisponível: cache compartilhado não configurado.")
    try:
        client = backend._cache.get_client(write=True)
        resultado = client.eval("""
            local t = redis.call('TIME')
            local segundo = tonumber(t[1])
            local millis = segundo * 1000 + math.floor(tonumber(t[2]) / 1000)
            local dia = math.floor(segundo / 86400)
            local diaria = KEYS[1] .. ':dia:' .. dia
            local taxa = KEYS[1] .. ':janela'
            if tonumber(redis.call('GET', diaria) or '0') >= tonumber(ARGV[1]) then return 0 end
            redis.call('ZREMRANGEBYSCORE', taxa, '-inf', millis - 1000)
            if redis.call('ZCARD', taxa) >= 5 then return 0 end
            local seq = redis.call('INCR', diaria)
            redis.call('EXPIRE', diaria, 172800)
            redis.call('ZADD', taxa, millis, tostring(seq) .. ':' .. tostring(millis))
            redis.call('EXPIRE', taxa, 3)
            return 1
        """, 1, "erp:geoapify:cota", max(0, int(_config("GEOAPIFY_DAILY_LIMIT", 2700))))
    except Exception as exc:
        raise MapsServiceError("Busca de endereço temporariamente indisponível.") from exc
    if not resultado:
        raise MapsServiceError("Limite de consultas de endereços atingido. Tente novamente mais tarde.")


def buscar_endereco(query, *, modo="autocomplete", contexto=None, localidade="", provider=None, metricas=None):
    """Busca endereços sem confirmar automaticamente nenhum resultado."""
    query = _preparar_busca(query)
    localidade = _preparar_busca(localidade)[:100]
    if len(query) > 300:
        raise MapsServiceError("O endereço deve ter até 300 caracteres.")
    query_normalizada = _normalizar_query(query)
    if not query_normalizada:
        raise MapsServiceError("Informe um endereço para buscar.")

    provider = (provider or _config("MAPS_GEOCODE_PROVIDER", "geoapify")).strip().lower()
    if modo not in {"autocomplete", "search"}:
        raise MapsServiceError("Tipo de busca inválido.")
    if modo == "autocomplete" and len(query_normalizada) < 4:
        return []
    if provider == "nominatim" and modo == "autocomplete":
        raise MapsServiceError("Autocomplete não disponível para este provedor.")
    if provider not in {"geoapify", "nominatim"}:
        raise MapsServiceError("Provedor de endereços não configurado.")
    cache_key = _cache_key("geocode:v4", (provider, modo, contexto, localidade, query_normalizada))
    marco = MARCO_KM.search(query)
    numero = re.search(r",\s*(\d+[a-zA-Z]?)(?=\s*(?:[-,]|$))", query)
    cep = re.search(r"\b\d{5}-\d{3}\b", query)
    cidade_digitada = re.search(r",\s*([^,\d]+?)\s*[-/]\s*([A-Z]{2})(?=,|$)", query)
    if not cidade_digitada and "," in query:
        final = query.rsplit(",", 1)[1].strip()
        if re.fullmatch(r"[^\W\d_]+(?:\s+[^\W\d_]+){0,4}", final):
            cidade_digitada = re.search(r"(.+)", final)
    cidade_prevista = cidade_digitada.group(1).strip() if cidade_digitada else re.split(r"\s*[-/]\s*(?=[A-Z]{2}$)", localidade)[0]
    via = re.match(r"^(?:rua|avenida|rodovia|travessa|praça)\s+([^,]+)", query, flags=re.I)

    def termos(texto):
        sem_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))
        return {t for t in re.findall(r"[a-z]+", sem_acentos) if len(t) > 2}

    def via_compativel(item):
        if not via or (cep and item.get("result_type") == "postcode"):
            return True
        esperados = termos(via.group(1))
        rua = item.get("street") or item.get("formatted") or ""
        encontrados = termos(rua)
        return not esperados or len(esperados & encontrados) >= (2 * len(esperados) + 2) // 3
    geo_cache = caches["geocoding"] if provider == "geoapify" and "geocoding" in settings.CACHES else cache
    try:
        cached = geo_cache.get(cache_key)
    except Exception as exc:
        raise MapsServiceError("Busca de endereço temporariamente indisponível.") from exc
    if cached is not None:
        return cached

    if provider == "geoapify":
        chave = _config("GEOAPIFY_API_KEY", "")
        if not chave:
            raise MapsServiceError("Busca de endereço não configurada.")
        params = {"text": query.strip(), "apiKey": chave, "filter": "countrycode:br", "lang": "pt", "limit": int(_config("MAPS_GEOCODE_LIMIT", 5))}
        if contexto:
            try:
                lat, lon = (_decimal_coord(v, "contexto") for v in contexto.split(","))
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    params["bias"] = f"proximity:{lon},{lat}"
            except (ValueError, MapsServiceError):
                raise MapsServiceError("Contexto geográfico inválido.")
        def consultar(parametros):
            _reservar_cota_geoapify()
            if metricas is not None:
                metricas["chamadas"] += 1
            try:
                response = requests.get(GEOAPIFY_URL + modo, params=parametros, timeout=_timeout())
                if response.status_code == 429:
                    raise MapsServiceError("Limite do serviço de endereços atingido. Tente novamente mais tarde.")
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Falha ao consultar o serviço de geocodificação.")
                raise MapsServiceError("Serviço de busca de endereço indisponível no momento.") from exc

        def precisao(item):
            rank = item.get("rank") or {}
            return (
                item.get("result_type") in {"building", "amenity"}
                and (rank.get("confidence") or 0) >= 0.8
                and rank.get("match_type") not in {"match_by_street", "match_by_city", "match_by_postcode"}
                and (not marco or marco.group(1) in _marcos_km(item))
                and (not numero or str(item.get("housenumber") or "").lower() == numero.group(1).lower())
                and (not cep or item.get("postcode") == cep.group())
                and not divergente(item)
                and via_compativel(item)
            )

        def propriedades(payload):
            return [f.get("properties") or {} for f in payload.get("features", [])]

        partes_local = re.split(r"\s*[-/]\s*(?=[A-Z]{2}$)", localidade, maxsplit=1)

        def divergente(item):
            municipio = item.get("city") or item.get("town")
            uf = item.get("state_code")
            uf_prevista = cidade_digitada.group(2) if cidade_digitada and cidade_digitada.lastindex == 2 else partes_local[1] if not cidade_digitada and len(partes_local) > 1 else ""
            return bool(cidade_prevista and (
                (municipio and _normalizar_query(cidade_prevista) != _normalizar_query(municipio))
                or (uf and uf_prevista and uf.upper() != uf_prevista)
            ))

        def relevancia(item):
            return (
                not divergente(item),
                bool(numero and str(item.get("housenumber") or "").lower() == numero.group(1).lower()),
                precisao(item),
                (item.get("rank") or {}).get("confidence") or 0,
            )

        itens = propriedades(consultar(params))
        def achou_numero(item):
            return bool(
                numero and str(item.get("housenumber") or "").lower() == numero.group(1).lower()
                and via_compativel(item) and not divergente(item)
                and (not cep or item.get("postcode") == cep.group())
                and ((item.get("rank") or {}).get("confidence") or 0) >= 0.7
            )

        refinamento_incompleto = False
        if modo == "search" and not marco and not any(precisao(i) or achou_numero(i) for i in itens):
            base = {k: v for k, v in params.items() if k != "text"}
            for tentativa in _tentativas_busca(query, localidade):
                try:
                    itens.extend(propriedades(consultar({**base, **tentativa})))
                except MapsServiceError:
                    if not itens:
                        raise
                    refinamento_incompleto = True
                    break
                if any(precisao(i) or achou_numero(i) for i in itens):
                    break
        resultados = []
        vistos = set()
        for item in sorted(itens, key=relevancia, reverse=True):
            if item.get("lat") is None or item.get("lon") is None:
                continue
            if not via_compativel(item):
                continue
            marcos = _marcos_km(item) if marco else set()
            if marco and marco.group(1) not in marcos:
                continue
            tipo = item.get("result_type", "")
            rank = item.get("rank") or {}
            chave_item = item.get("place_id") or (item["lat"], item["lon"], item.get("formatted"))
            if chave_item in vistos:
                continue
            vistos.add(chave_item)
            municipio = item.get("city") or item.get("town") or ""
            uf = item.get("state_code") or ""
            resultados.append({
                "display_name": item.get("formatted", ""),
                "lat": str(round(float(item["lat"]), 7)),
                "lon": str(round(float(item["lon"]), 7)),
                "result_type": tipo,
                "rank": rank,
                "aproximado": not precisao(item),
                "nome": item.get("name") or item.get("street") or "",
                "numero": item.get("housenumber") or "",
                "bairro": item.get("suburb") or item.get("district") or "",
                "municipio": municipio,
                "uf": uf,
                "cep": item.get("postcode") or "",
                "divergente": divergente(item),
            })
        resultados = resultados[:params["limit"]]
        if not refinamento_incompleto:
            try:
                geo_cache.set(cache_key, resultados, _cache_ttl())
            except Exception:
                logger.warning("Não foi possível gravar o cache de geocodificação.")
        return resultados

    params = {
        "q": query_normalizada,
        "format": "jsonv2",
        "limit": int(_config("MAPS_GEOCODE_LIMIT", 5)),
        "countrycodes": _config("MAPS_COUNTRYCODES", "br"),
        "addressdetails": 1,
    }
    headers = {"User-Agent": _user_agent()}

    try:
        if metricas is not None:
            metricas["chamadas"] += 1
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
            "lat": str(round(float(item["lat"]), 7)),
            "lon": str(round(float(item["lon"]), 7)),
            "nome": item.get("name") or "",
            "numero": endereco.get("house_number") or "",
            "bairro": endereco.get("suburb") or endereco.get("neighbourhood") or "",
            "municipio": endereco.get("city") or endereco.get("town") or endereco.get("municipality") or "",
            "uf": (endereco.get("ISO3166-2-lvl4") or endereco.get("state") or "").removeprefix("BR-"),
            "cep": endereco.get("postcode") or "",
            "result_type": item.get("addresstype") or item.get("type") or "",
            "aproximado": not bool(endereco.get("house_number")),
        }
        for item in payload
        if item.get("lat") and item.get("lon")
        if not marco or marco.group(1) in _marcos_km(item)
        for endereco in [item.get("address") or {}]
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
