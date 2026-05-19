from django.conf import settings
from django.core.cache import cache


PREFIXO_CACHE = "identidade:ldap"


def _normalizar_username(username):
    return (username or "").strip().casefold()


def _chave(tipo, username):
    return f"{PREFIXO_CACHE}:{tipo}:{_normalizar_username(username)}"


def obter_usuario_existe_ad(username):
    valor = cache.get(_chave("usuario_existe", username))
    if not isinstance(valor, dict) or "existe" not in valor:
        return None
    return bool(valor["existe"])


def gravar_usuario_existe_ad(username, existe, timeout=None):
    cache.set(
        _chave("usuario_existe", username),
        {"existe": bool(existe)},
        timeout or getattr(settings, "LDAP_USER_EXISTS_CACHE_TIMEOUT", 300),
    )


def obter_snapshot_diretorio(username):
    valor = cache.get(_chave("snapshot", username))
    if not isinstance(valor, dict):
        return None
    return valor


def gravar_snapshot_diretorio(username, dados, timeout=None):
    cache.set(
        _chave("snapshot", username),
        dict(dados or {}),
        timeout or getattr(settings, "LDAP_DIRECTORY_CACHE_TIMEOUT", 300),
    )


def invalidar_identidade_usuario(username):
    cache.delete(_chave("usuario_existe", username))
    cache.delete(_chave("snapshot", username))
