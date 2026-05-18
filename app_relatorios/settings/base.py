"""
Settings base — compartilhado entre todos os ambientes.
Não use este arquivo diretamente. Use dev.py ou prod.py.
"""

import json
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
    "django.contrib.messages.middleware.MessageMiddleware",
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


# ─── Diretórios operacionais ─────────────────────────────────────────────────
LOG_DIR = BASE_DIR / "logs"


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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG",
    },
    "loggers": {
        "django_auth_ldap": {
            "handlers": ["console"],
            "level": "DEBUG" if LDAP_AUTH_ENABLED else "INFO",
            "propagate": False,
        },
        "relatorios.services.identidade": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
