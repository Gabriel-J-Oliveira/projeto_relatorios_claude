"""
Settings base — compartilhado entre todos os ambientes.
Não use este arquivo diretamente. Use dev.py ou prod.py.
"""

import json
from pathlib import Path

from decouple import config

# ─── Diretório raiz do projeto ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ─── Segurança ────────────────────────────────────────────────────────────────
SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-me-in-production")
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=lambda v: [s.strip() for s in v.split(",")])


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
            ],
        },
    },
]


# ─── Banco de dados ───────────────────────────────────────────────────────────
# Padrão SQLite — sobrescrito em prod.py para PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": config(
            "DB_ENGINE",
            default="django.db.backends.sqlite3"
        ),
        "NAME": config(
            "DB_NAME",
            default=BASE_DIR / "db.sqlite3"
        ),
        "USER": config("DB_USER", default=""),
        "PASSWORD": config("DB_PASSWORD", default=""),
        "HOST": config("DB_HOST", default=""),
        "PORT": config("DB_PORT", default=""),
    }
}

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
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# ─── Arquivos de mídia (uploads) ──────────────────────────────────────────────
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


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

if LDAP_AUTH_ENABLED:
    import ldap
    from django_auth_ldap.config import LDAPSearch, NestedActiveDirectoryGroupType

    AUTH_LDAP_SERVER_URI = LDAP_SERVER_URI
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
