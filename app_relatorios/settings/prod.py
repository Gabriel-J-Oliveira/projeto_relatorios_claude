"""
Settings de producao.

Usado pelo WSGI/Gunicorn:
gunicorn app_relatorios.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
"""

from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa


DEBUG = False

if not config("SECRET_KEY", default="") or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("SECRET_KEY seguro deve ser definido no ambiente de producao.")

MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
ANEXOS_ROOT.mkdir(parents=True, exist_ok=True)
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,relatorios.control.local",
    cast=lambda v: [host.strip() for host in v.split(",") if host.strip()],
)

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="",
    cast=lambda v: [origin.strip() for origin in v.split(",") if origin.strip()],
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Em HTTP puro, cookies Secure nao sao armazenados/enviados pelo navegador.
# Ative como True no .env quando o Nginx estiver servindo HTTPS.
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)

SECURE_CONTENT_TYPE_NOSNIFF = config("SECURE_CONTENT_TYPE_NOSNIFF", default=True, cast=bool)
X_FRAME_OPTIONS = config("X_FRAME_OPTIONS", default="SAMEORIGIN")


def _database_from_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL deve usar postgres:// ou postgresql://.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote((parsed.path or "").lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "127.0.0.1",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": config("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
    }


DATABASE_URL = config("DATABASE_URL", default="")
POSTGRES_NAME = config("POSTGRES_DB", default=config("DB_NAME", default=""))
POSTGRES_USER_VALUE = config("POSTGRES_USER", default=config("DB_USER", default=""))
POSTGRES_PASSWORD_VALUE = config("POSTGRES_PASSWORD", default=config("DB_PASSWORD", default=""))
POSTGRES_HOST_VALUE = config("POSTGRES_HOST", default=config("DB_HOST", default="127.0.0.1"))
POSTGRES_PORT_VALUE = config("POSTGRES_PORT", default=config("DB_PORT", default="5432"))

if not DATABASE_URL and not POSTGRES_NAME:
    raise ImproperlyConfigured("Defina DATABASE_URL ou POSTGRES_DB/DB_NAME no ambiente de producao.")

DATABASES = {
    "default": _database_from_url(DATABASE_URL)
    if DATABASE_URL
    else {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": POSTGRES_NAME,
        "USER": POSTGRES_USER_VALUE,
        "PASSWORD": POSTGRES_PASSWORD_VALUE,
        "HOST": POSTGRES_HOST_VALUE,
        "PORT": POSTGRES_PORT_VALUE,
        "CONN_MAX_AGE": config("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
    }
}

if not LOG_FILES_ENABLED:
    LOGGING["loggers"]["django.security"]["handlers"] = ["console"]
    LOGGING["loggers"]["django.request"]["handlers"] = ["console"]
