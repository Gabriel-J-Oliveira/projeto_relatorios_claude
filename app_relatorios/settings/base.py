"""
Settings base — compartilhado entre todos os ambientes.
Não use este arquivo diretamente. Use dev.py ou prod.py.
"""

import json
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from decouple import config

# ─── Diretório raiz do projeto ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ─── Segurança ────────────────────────────────────────────────────────────────
SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-me-in-production")
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1",
    cast=lambda v: [s.strip() for s in v.split(",") if s.strip()],
)


# ─── Apps instalados ──────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "crispy_forms",
    "crispy_bootstrap5",
    "django_filters",
]

LOCAL_APPS = [
    "relatorios",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


# ─── Middleware ───────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",          # estáticos em produção
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "relatorios.middleware.IdentidadeCorporativaMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "relatorios.middleware.CadastroObrigatorioMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ─── URLs e WSGI ─────────────────────────────────────────────────────────────
ROOT_URLCONF = "app_relatorios.urls"
WSGI_APPLICATION = "app_relatorios.wsgi.application"


# ─── Templates ───────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],           # templates na raiz do projeto
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.media",
                "relatorios.context_processors.permissoes_erp",
            ],
        },
    },
]


# ─── Validação de senhas ──────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ─── Internacionalização ──────────────────────────────────────────────────────
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# ─── Arquivos estáticos ───────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# ─── Arquivos de mídia (uploads) ──────────────────────────────────────────────
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Arquivos anexados por usuários, como comprovantes de despesa/KM.
# Mantido separado de MEDIA_ROOT para preservar /media/ apenas para assets internos.
ANEXOS_URL = config("ANEXOS_URL", default="/anexos/")
ANEXOS_ROOT = Path(config("ANEXOS_ROOT", default="/home/app_relatorios_files"))
ANEXO_MAX_UPLOAD_MB = config("ANEXO_MAX_UPLOAD_MB", default=10, cast=int)
VALOR_KM_CONTROLSUL = config("VALOR_KM_CONTROLSUL", default="1.35")

# Integracao de clientes ControlSul. O token deve vir apenas do ambiente.
CLIENTES_API_URL = config("CLIENTES_API_URL", default="https://api.controlsul.com/clients")
CLIENTES_API_TOKEN = config("CLIENTES_API_TOKEN", default="")
CLIENTES_API_TIMEOUT = config("CLIENTES_API_TIMEOUT", default=30, cast=int)
CLIENTES_API_ENABLED = config("CLIENTES_API_ENABLED", default=True, cast=bool)


# Email interno / SMTP. Em desenvolvimento, dev.py pode sobrescrever para console.
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=15, cast=int)
DEFAULT_FROM_EMAIL = config(
    "DEFAULT_FROM_EMAIL",
    default=EMAIL_HOST_USER or "naoresponda@controlsul.com.br",
)
APP_BASE_URL = config("APP_BASE_URL", default="")
EMAIL_DESTINATARIOS_FINALIZACAO_EXTRA = config(
    "EMAIL_DESTINATARIOS_FINALIZACAO_EXTRA",
    default="",
    cast=lambda v: [email.strip() for email in v.split(",") if email.strip()],
)


# ─── Diretórios operacionais ─────────────────────────────────────────────────
APP_LOG_LEVEL = config("APP_LOG_LEVEL", default="INFO").upper()
LOG_DIR = Path(config("APP_LOG_DIR", default=str(BASE_DIR / "logs")))


def _preparar_diretorio_logs(path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


LOG_FILES_ENABLED = _preparar_diretorio_logs(LOG_DIR)


# Sessao corporativa: 60 minutos de inatividade.
SESSION_COOKIE_AGE = config("SESSION_COOKIE_AGE", default=3600, cast=int)
SESSION_SAVE_EVERY_REQUEST = config("SESSION_SAVE_EVERY_REQUEST", default=True, cast=bool)
SESSION_EXPIRE_AT_BROWSER_CLOSE = config(
    "SESSION_EXPIRE_AT_BROWSER_CLOSE",
    default=False,
    cast=bool,
)

# Protecoes HTTP parametrizaveis por ambiente. HTTPS/HSTS ficam desligados por
# padrao para nao quebrar desenvolvimento local; habilite no .env de producao.
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
SESSION_COOKIE_HTTPONLY = config("SESSION_COOKIE_HTTPONLY", default=True, cast=bool)
SESSION_COOKIE_SAMESITE = config("SESSION_COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SAMESITE = config("CSRF_COOKIE_SAMESITE", default="Lax")
SECURE_CONTENT_TYPE_NOSNIFF = config("SECURE_CONTENT_TYPE_NOSNIFF", default=True, cast=bool)
X_FRAME_OPTIONS = config("X_FRAME_OPTIONS", default="DENY")
SECURE_REFERRER_POLICY = config("SECURE_REFERRER_POLICY", default="same-origin")
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS",
    default=False,
    cast=bool,
)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)


# Cache operacional. Em producao multiworker pode ser substituido por Redis/Memcached
# via prod.py/env sem alterar a aplicacao.
CACHES = {
    "default": {
        "BACKEND": config(
            "CACHE_BACKEND",
            default="django.core.cache.backends.locmem.LocMemCache",
        ),
        "LOCATION": config("CACHE_LOCATION", default="erp-relatorios-cache"),
    }
}
DASHBOARD_CACHE_TTL = config("DASHBOARD_CACHE_TTL", default=180, cast=int)


# ─── Chave primária padrão ────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ─── Crispy Forms ────────────────────────────────────────────────────────────
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# ─── Redirecionamento de login ────────────────────────────────────────────────
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

# ─── Integração futura com AD/LDAP ────────────────────────────────────────────
# Mapeamento declarativo de grupos externos para grupos formais do ERP.
# A autenticação LDAP ainda não usa esta configuração; ela alimenta apenas a
# camada desacoplada de sincronização preparada em relatorios.services.identidade.
#
# Exemplo futuro:
# AD_GROUP_MAPPING = {
#     "CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local": "Financeiro",
#     "ERP-Tecnicos": "Tecnico",
# }
AD_GROUP_MAPPING = config("AD_GROUP_MAPPING", default="{}", cast=json.loads)

# ─── Autenticação LDAP/Active Directory ───────────────────────────────────────
# O backend LDAP fica antes do ModelBackend, mas só tenta autenticar quando
# LDAP_AUTH_ENABLED=True. Com a flag desligada, o login local Django segue igual.
AUTHENTICATION_BACKENDS = [
    "relatorios.services.identidade.ldap_backend.ActiveDirectoryBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LDAP_AUTH_ENABLED = config("LDAP_AUTH_ENABLED", default=False, cast=bool)
LDAP_SERVER_URI = config("LDAP_SERVER_URI", default="")
LDAP_SERVER_URIS = config(
    "LDAP_SERVER_URIS",
    default=LDAP_SERVER_URI,
    cast=lambda v: [uri.strip() for uri in v.split(",") if uri.strip()],
)
LDAP_BIND_DN = config("LDAP_BIND_DN", default="")
LDAP_BIND_PASSWORD = config("LDAP_BIND_PASSWORD", default="")
LDAP_USER_SEARCH_BASE_DN = config("LDAP_USER_SEARCH_BASE_DN", default="")
LDAP_USER_SEARCH_FILTER = config(
    "LDAP_USER_SEARCH_FILTER",
    default="(sAMAccountName=%(user)s)",
)
LDAP_GROUP_SEARCH_BASE_DN = config("LDAP_GROUP_SEARCH_BASE_DN", default="")
LDAP_GROUP_SEARCH_FILTER = config("LDAP_GROUP_SEARCH_FILTER", default="(objectClass=group)")
LDAP_REQUIRE_GROUP = config("LDAP_REQUIRE_GROUP", default="")
LDAP_ACTIVE_DIRECTORY_DOMAIN = config("LDAP_ACTIVE_DIRECTORY_DOMAIN", default="")
LDAP_NORMALIZE_USERNAME = config("LDAP_NORMALIZE_USERNAME", default=True, cast=bool)
LDAP_START_TLS = config("LDAP_START_TLS", default=False, cast=bool)
LDAP_DISABLE_REFERRALS = config("LDAP_DISABLE_REFERRALS", default=True, cast=bool)
LDAP_NETWORK_TIMEOUT = config("LDAP_NETWORK_TIMEOUT", default=5, cast=int)
LDAP_OPERATION_TIMEOUT = config("LDAP_OPERATION_TIMEOUT", default=10, cast=int)
LDAP_TLS_REQUIRE_CERT = config("LDAP_TLS_REQUIRE_CERT", default="DEMAND").upper()
LDAP_CA_CERT_FILE = config("LDAP_CA_CERT_FILE", default="")
LDAP_CA_CERT_DIR = config("LDAP_CA_CERT_DIR", default="")
LDAP_USER_EXISTS_CACHE_TIMEOUT = config(
    "LDAP_USER_EXISTS_CACHE_TIMEOUT",
    default=300,
    cast=int,
)
LDAP_DIRECTORY_CACHE_TIMEOUT = config(
    "LDAP_DIRECTORY_CACHE_TIMEOUT",
    default=300,
    cast=int,
)
LDAP_SESSION_REVALIDATE_SECONDS = config(
    "LDAP_SESSION_REVALIDATE_SECONDS",
    default=300,
    cast=int,
)
LDAP_BLOCK_USERS_WITHOUT_ERP_GROUP = config(
    "LDAP_BLOCK_USERS_WITHOUT_ERP_GROUP",
    default=True,
    cast=bool,
)


def _validar_configuracao_ldap():
    obrigatorias = {
        "LDAP_SERVER_URI ou LDAP_SERVER_URIS": LDAP_SERVER_URI or LDAP_SERVER_URIS,
        "LDAP_BIND_DN": LDAP_BIND_DN,
        "LDAP_BIND_PASSWORD": LDAP_BIND_PASSWORD,
        "LDAP_USER_SEARCH_BASE_DN": LDAP_USER_SEARCH_BASE_DN,
    }
    ausentes = [nome for nome, valor in obrigatorias.items() if not valor]
    if ausentes:
        raise ImproperlyConfigured(
            "LDAP_AUTH_ENABLED=True, mas faltam variaveis obrigatorias: "
            + ", ".join(ausentes)
        )

if LDAP_AUTH_ENABLED:
    _validar_configuracao_ldap()

    import ldap
    from django_auth_ldap.config import LDAPSearch, NestedActiveDirectoryGroupType

    AUTH_LDAP_SERVER_URI = LDAP_SERVER_URIS[0]
    AUTH_LDAP_BIND_DN = LDAP_BIND_DN
    AUTH_LDAP_BIND_PASSWORD = LDAP_BIND_PASSWORD
    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        LDAP_USER_SEARCH_BASE_DN,
        ldap.SCOPE_SUBTREE,
        LDAP_USER_SEARCH_FILTER,
    )
    AUTH_LDAP_ALWAYS_UPDATE_USER = True
    AUTH_LDAP_USER_ATTR_MAP = {
        "first_name": "givenName",
        "last_name": "sn",
        "email": "mail",
    }
    AUTH_LDAP_USER_ATTRLIST = [
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
    ]
    AUTH_LDAP_MIRROR_GROUPS = False
    AUTH_LDAP_FIND_GROUP_PERMS = False
    AUTH_LDAP_START_TLS = LDAP_START_TLS

    AUTH_LDAP_CONNECTION_OPTIONS = {}
    if LDAP_DISABLE_REFERRALS:
        AUTH_LDAP_CONNECTION_OPTIONS[ldap.OPT_REFERRALS] = 0
    AUTH_LDAP_CONNECTION_OPTIONS[ldap.OPT_NETWORK_TIMEOUT] = LDAP_NETWORK_TIMEOUT
    AUTH_LDAP_CONNECTION_OPTIONS[ldap.OPT_TIMEOUT] = LDAP_OPERATION_TIMEOUT

    _LDAP_CERT_POLICY = {
        "NEVER": ldap.OPT_X_TLS_NEVER,
        "ALLOW": ldap.OPT_X_TLS_ALLOW,
        "TRY": ldap.OPT_X_TLS_TRY,
        "DEMAND": ldap.OPT_X_TLS_DEMAND,
        "HARD": ldap.OPT_X_TLS_HARD,
    }
    AUTH_LDAP_GLOBAL_OPTIONS = {
        ldap.OPT_X_TLS_REQUIRE_CERT: _LDAP_CERT_POLICY.get(
            LDAP_TLS_REQUIRE_CERT,
            ldap.OPT_X_TLS_DEMAND,
        ),
    }
    if LDAP_CA_CERT_FILE:
        AUTH_LDAP_GLOBAL_OPTIONS[ldap.OPT_X_TLS_CACERTFILE] = LDAP_CA_CERT_FILE
    if LDAP_CA_CERT_DIR:
        AUTH_LDAP_GLOBAL_OPTIONS[ldap.OPT_X_TLS_CACERTDIR] = LDAP_CA_CERT_DIR

    if LDAP_GROUP_SEARCH_BASE_DN:
        AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
            LDAP_GROUP_SEARCH_BASE_DN,
            ldap.SCOPE_SUBTREE,
            LDAP_GROUP_SEARCH_FILTER,
        )
        AUTH_LDAP_GROUP_TYPE = NestedActiveDirectoryGroupType()
        if LDAP_REQUIRE_GROUP:
            AUTH_LDAP_REQUIRE_GROUP = LDAP_REQUIRE_GROUP

LOG_FORMAT_VERBOSE = (
    "[{asctime}] [{levelname}] {name} {module}.{funcName}:{lineno} - {message}"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": LOG_FORMAT_VERBOSE,
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "console": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
            "level": "DEBUG",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": APP_LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django_auth_ldap": {
            "handlers": ["console"],
            "level": "DEBUG" if LDAP_AUTH_ENABLED and APP_LOG_LEVEL == "DEBUG" else "INFO",
            "propagate": False,
        },
        "relatorios": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.middleware": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.identidade": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.email_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.clientes_api_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.clientes_sync_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.maps_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.pdf_cliente_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.pdf_interno_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.workflow_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
        "relatorios.services.snapshot_service": {
            "handlers": ["console"],
            "level": APP_LOG_LEVEL,
            "propagate": False,
        },
    },
}


def _adicionar_file_handler(nome, arquivo, level="INFO"):
    LOGGING["handlers"][nome] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / arquivo),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 10,
        "formatter": "verbose",
        "level": level,
        "encoding": "utf-8",
    }


if LOG_FILES_ENABLED:
    _adicionar_file_handler("app_file", "app_relatorios.log", APP_LOG_LEVEL)
    _adicionar_file_handler("errors_file", "errors.log", "ERROR")
    _adicionar_file_handler("emails_file", "emails.log", APP_LOG_LEVEL)
    _adicionar_file_handler("maps_file", "maps.log", APP_LOG_LEVEL)
    _adicionar_file_handler("pdfs_file", "pdfs.log", APP_LOG_LEVEL)
    _adicionar_file_handler("security_file", "security.log", "WARNING")

    LOGGING["root"]["handlers"] = ["console", "app_file", "errors_file"]
    LOGGING["loggers"]["django"]["handlers"] = ["console", "app_file", "errors_file"]
    LOGGING["loggers"]["django.request"]["handlers"] = ["console", "errors_file"]
    LOGGING["loggers"]["django.security"]["handlers"] = ["console", "security_file", "errors_file"]
    LOGGING["loggers"]["security"]["handlers"] = ["console", "security_file", "errors_file"]
    LOGGING["loggers"]["relatorios"]["handlers"] = ["console", "app_file", "errors_file"]
    LOGGING["loggers"]["relatorios.middleware"]["handlers"] = ["console", "security_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.identidade"]["handlers"] = ["console", "security_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.email_service"]["handlers"] = ["console", "emails_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.clientes_api_service"]["handlers"] = ["console", "app_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.clientes_sync_service"]["handlers"] = ["console", "app_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.maps_service"]["handlers"] = ["console", "maps_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.pdf_cliente_service"]["handlers"] = ["console", "pdfs_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.pdf_interno_service"]["handlers"] = ["console", "pdfs_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.workflow_service"]["handlers"] = ["console", "app_file", "security_file", "errors_file"]
    LOGGING["loggers"]["relatorios.services.snapshot_service"]["handlers"] = ["console", "app_file", "pdfs_file", "errors_file"]
