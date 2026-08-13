import logging
import time
import uuid
from contextlib import contextmanager

from django.conf import settings


logger = logging.getLogger("relatorios")


def _bool_setting(nome, default=False):
    return bool(getattr(settings, nome, default))


def _float_setting(nome, default):
    try:
        return float(getattr(settings, nome, default))
    except (TypeError, ValueError):
        return float(default)


def _int_post(post, chave):
    try:
        return int(post.get(chave) or 0)
    except (TypeError, ValueError):
        return 0


def _contar_uploads(files):
    quantidade = 0
    total_bytes = 0
    for _, arquivos in files.lists():
        for arquivo in arquivos:
            if not getattr(arquivo, "name", ""):
                continue
            quantidade += 1
            try:
                total_bytes += int(getattr(arquivo, "size", 0) or 0)
            except (TypeError, ValueError):
                pass
    return quantidade, total_bytes


class RelatorioPerformanceTracker:
    """
    Coleta timings temporarios do POST do relatorio sem interferir no fluxo.

    Com RELATORIO_PERF_ENABLED=False, os metodos viram no-op para manter
    overhead desprezivel.
    """

    def __init__(self, request, instance=None):
        self.enabled = _bool_setting("RELATORIO_PERF_ENABLED", False) and request.method == "POST"
        self.slow_seconds = _float_setting("RELATORIO_PERF_SLOW_SECONDS", 3.0)
        self.request_id = uuid.uuid4().hex[:8]
        self.started = time.perf_counter()
        self.timings = {}
        self.report_id = getattr(instance, "pk", None) or ""
        self.action = "edit" if instance else "create"
        self.http_status = ""
        self.outcome = "unknown"
        self._finished = False
        self.files = 0
        self.upload_bytes = 0
        self.expenses = 0
        self.km = 0
        self.participants = 0
        if self.enabled:
            self.files, self.upload_bytes = _contar_uploads(request.FILES)
            self.expenses = _int_post(request.POST, "despesas-TOTAL_FORMS")
            self.km = _int_post(request.POST, "trechos-TOTAL_FORMS")
            self.participants = len(request.POST.getlist("tecnicos_equipe"))
            if request.POST.get("tecnico_responsavel"):
                self.participants += 1

    @contextmanager
    def phase(self, name):
        if not self.enabled:
            yield
            return
        inicio = time.perf_counter()
        try:
            yield
        finally:
            duracao_ms = (time.perf_counter() - inicio) * 1000
            self.timings[name] = self.timings.get(name, 0.0) + duracao_ms

    def set_report(self, relatorio):
        if self.enabled and relatorio is not None:
            self.report_id = getattr(relatorio, "pk", None) or self.report_id

    def finish(self, *, outcome, http_status=None, relatorio=None):
        if not self.enabled or self._finished:
            return
        self._finished = True
        self.outcome = outcome
        self.http_status = http_status or self.http_status
        self.set_report(relatorio)
        total_ms = (time.perf_counter() - self.started) * 1000
        slow = int(total_ms >= (self.slow_seconds * 1000))
        partes = [
            "[RELATORIO_PERF]",
            f"request_id={self.request_id}",
            f"action={self.action}",
            f"report_id={self.report_id or ''}",
            f"outcome={self.outcome}",
            f"http_status={self.http_status or ''}",
            f"total_ms={total_ms:.0f}",
        ]
        for nome in sorted(self.timings):
            partes.append(f"{nome}_ms={self.timings[nome]:.0f}")
        partes.extend(
            [
                f"files={self.files}",
                f"upload_bytes={self.upload_bytes}",
                f"expenses={self.expenses}",
                f"km={self.km}",
                f"participants={self.participants}",
                f"slow={slow}",
            ]
        )
        mensagem = " ".join(partes)
        if slow:
            logger.warning(mensagem)
        else:
            logger.info(mensagem)

    def apply_server_timing(self, response):
        if not self.enabled or response is None:
            return response
        metricas = []
        for nome in sorted(self.timings):
            metricas.append(f"{nome};dur={self.timings[nome]:.0f}")
        if metricas:
            response["Server-Timing"] = ", ".join(metricas)
        return response
