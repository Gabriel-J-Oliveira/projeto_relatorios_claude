from django.apps import AppConfig


class RelatoriosConfig(AppConfig):
    name = 'relatorios'

    def ready(self):
        import relatorios.signals  # noqa: F401
