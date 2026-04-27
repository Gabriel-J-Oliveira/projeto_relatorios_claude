"""
Settings de desenvolvimento local.
Ative com: export DJANGO_SETTINGS_MODULE= app_relatorios.settings.dev
"""

from .base import *  # noqa

DEBUG = True

# Em dev, aceitar qualquer host
ALLOWED_HOSTS = ["*"]

# Mostrar emails no terminal durante desenvolvimento
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Django Debug Toolbar (opcional — instale se quiser: pip install django-debug-toolbar)
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
# INTERNAL_IPS = ["127.0.0.1"]