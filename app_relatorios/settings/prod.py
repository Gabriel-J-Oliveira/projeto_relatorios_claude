"""
Settings de produção.

Usado pelo WSGI/Gunicorn:
gunicorn app_relatorios.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa


DEBUG = False

MEDIA_ROOT = Path(config("MEDIA_ROOT", default="/home/app_relatorios_files"))

LOG_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
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

# Em HTTP puro, cookies Secure não são armazenados/enviados pelo navegador.
# Ative como True no .env quando o Nginx estiver servindo HTTPS.
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not config("POSTGRES_DB", default=""):
    raise ImproperlyConfigured("POSTGRES_DB deve ser definido no ambiente de produção.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB"),
        "USER": config("POSTGRES_USER"),
        "PASSWORD": config("POSTGRES_PASSWORD"),
        "HOST": config("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": config("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": config("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
    }
}

LOGGING["handlers"]["file"] = {
    "class": "logging.handlers.RotatingFileHandler",
    "filename": LOG_DIR / "django.log",
    "maxBytes": 10 * 1024 * 1024,
    "backupCount": 5,
    "formatter": "verbose",
}
LOGGING["formatters"] = {
    "verbose": {
        "format": "{levelname} {asctime} {name} {process:d} {thread:d} {message}",
        "style": "{",
    },
}
LOGGING["root"]["handlers"] = ["console", "file"]
LOGGING["loggers"]["django.request"] = {
    "handlers": ["console", "file"],
    "level": "ERROR",
    "propagate": False,
}
