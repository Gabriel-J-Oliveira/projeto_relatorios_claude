bind = "127.0.0.1:8000"
workers = 3
timeout = 60

accesslog = "logs/gunicorn-access.log"
errorlog = "logs/gunicorn-error.log"
loglevel = "info"

wsgi_app = "app_relatorios.wsgi:application"
