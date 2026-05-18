bind = "127.0.0.1:8000"
workers = 3
timeout = 60

accesslog = "logs/gunicorn-access.log"
errorlog = "logs/gunicorn-error.log"
loglevel = "info"

# Protecao de deploy: Gunicorn em producao deve sempre forcar settings.prod,
# mesmo que wsgi.py use settings.dev como padrao da homologacao.
raw_env = ["DJANGO_SETTINGS_MODULE=app_relatorios.settings.prod"]

wsgi_app = "app_relatorios.wsgi:application"
