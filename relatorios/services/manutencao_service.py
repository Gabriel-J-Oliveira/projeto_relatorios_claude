import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone

from relatorios.models import EmailLog, StatusEmailLog


logger = logging.getLogger("relatorios.manutencao")

LOG_LINE_RE = re.compile(
    r"^\[(?P<data>[^\]]+)\]\s+\[(?P<nivel>[A-Z]+)\]\s+(?P<logger>\S+)\s+"
    r"(?P<origem>\S+)\s+-\s+(?P<mensagem>.*)$"
)

SENSITIVE_PATTERNS = [
    re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"((?:password|senha|token|secret_key|smtp password)\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
    re.compile(r"((?:EMAIL_HOST_PASSWORD|LDAP_BIND_PASSWORD|DB_PASSWORD)\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
]


@dataclass
class ResultadoReenvio:
    enviados: int = 0
    falhas: int = 0
    ignorados: int = 0
    mensagens: list | None = None

    def as_dict(self):
        return {
            "enviados": self.enviados,
            "falhas": self.falhas,
            "ignorados": self.ignorados,
            "mensagens": self.mensagens or [],
        }


def _log_dir():
    return Path(getattr(settings, "LOG_DIR", None) or getattr(settings, "APP_LOG_DIR", "") or "logs")


def arquivos_log_disponiveis():
    base = _log_dir()
    if not base.exists() or not base.is_dir():
        return []
    arquivos = [
        path
        for path in base.iterdir()
        if path.is_file() and path.suffix.lower() in {".log", ".txt"}
    ]
    return sorted(arquivos, key=lambda p: p.stat().st_mtime, reverse=True)


def mascarar_segredos(texto):
    resultado = str(texto or "")
    for pattern in SENSITIVE_PATTERNS:
        resultado = pattern.sub(r"\1****", resultado)
    return resultado


def _parse_data(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def _linha_para_dict(linha, arquivo):
    linha = mascarar_segredos(linha.rstrip("\n"))
    match = LOG_LINE_RE.match(linha)
    if not match:
        return {
            "data_hora": "",
            "nivel": "",
            "logger": "",
            "origem": arquivo.name,
            "mensagem": linha,
            "arquivo": arquivo.name,
            "raw": linha,
        }
    dados = match.groupdict()
    return {
        "data_hora": dados["data"],
        "nivel": dados["nivel"],
        "logger": dados["logger"],
        "origem": dados["origem"],
        "mensagem": dados["mensagem"],
        "arquivo": arquivo.name,
        "raw": linha,
    }


def _data_linha(entry):
    if not entry.get("data_hora"):
        return None
    try:
        return datetime.strptime(entry["data_hora"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def buscar_logs(*, data_inicio=None, data_fim=None, nivel="", termo="", logger_nome="", limite=200):
    limite = max(50, min(int(limite or 200), 1000))
    data_inicio = _parse_data(data_inicio)
    data_fim = _parse_data(data_fim)
    nivel = str(nivel or "").strip().upper()
    termo = str(termo or "").strip().lower()
    logger_nome = str(logger_nome or "").strip().lower()
    arquivos = arquivos_log_disponiveis()[:8]
    buffer = deque(maxlen=limite)

    for arquivo in reversed(arquivos):
        try:
            with arquivo.open("r", encoding="utf-8", errors="replace") as handle:
                for linha in handle:
                    entry = _linha_para_dict(linha, arquivo)
                    dt = _data_linha(entry)
                    if data_inicio and dt and dt.date() < data_inicio:
                        continue
                    if data_fim and dt and dt.date() > data_fim:
                        continue
                    if nivel and entry.get("nivel") != nivel:
                        continue
                    if logger_nome and logger_nome not in entry.get("logger", "").lower():
                        continue
                    if termo and termo not in entry.get("raw", "").lower():
                        continue
                    buffer.append(entry)
        except OSError as exc:
            logger.warning("falha_ler_log arquivo=%s erro=%s", arquivo, exc)

    linhas = list(buffer)
    linhas.reverse()
    return {
        "linhas": linhas,
        "arquivos": [arquivo.name for arquivo in arquivos],
        "log_dir": str(_log_dir()),
        "limite": limite,
    }


def filtrar_emails(params):
    qs = EmailLog.objects.select_related("relatorio", "reenviado_por").all()
    status = (params.get("email_status") or "").strip()
    tipo = (params.get("email_tipo") or "").strip()
    destinatario = (params.get("email_destinatario") or "").strip().lower()
    relatorio = (params.get("email_relatorio") or "").strip()
    data_inicio = _parse_data(params.get("email_data_inicio"))
    data_fim = _parse_data(params.get("email_data_fim"))

    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status__in=[StatusEmailLog.PENDENTE, StatusEmailLog.FALHA])
    if tipo:
        qs = qs.filter(tipo__icontains=tipo)
    if destinatario:
        qs = qs.filter(destinatarios__icontains=destinatario)
    if relatorio:
        filtro = Q(relatorio__numero__icontains=relatorio)
        if relatorio.isdigit():
            filtro |= Q(relatorio_id=int(relatorio))
        qs = qs.filter(filtro)
    if data_inicio:
        qs = qs.filter(criado_em__date__gte=data_inicio)
    if data_fim:
        qs = qs.filter(criado_em__date__lte=data_fim)
    return qs.order_by("-criado_em")


def resumo_emails():
    hoje = timezone.localdate()
    return {
        "pendentes": EmailLog.objects.filter(status=StatusEmailLog.PENDENTE).count(),
        "falhas": EmailLog.objects.filter(status=StatusEmailLog.FALHA).count(),
        "enviados_hoje": EmailLog.objects.filter(status=StatusEmailLog.ENVIADO, enviado_em__date=hoje).count(),
    }


def reenviar_email_log(email_log, usuario=None, permitir_enviado=False):
    if email_log.status == StatusEmailLog.ENVIADO and not permitir_enviado:
        raise ValueError("E-mail já enviado não pode ser reenviado por esta ação.")

    destinatarios = [email for email in (email_log.destinatarios or []) if email]
    if not destinatarios:
        email_log.status = StatusEmailLog.FALHA
        email_log.tentativas = (email_log.tentativas or 0) + 1
        email_log.ultimo_erro = "Sem destinatários válidos para reenvio."
        email_log.reenviado_por = usuario if getattr(usuario, "pk", None) else None
        email_log.ultimo_reenvio_em = timezone.now()
        email_log.save(update_fields=["status", "tentativas", "ultimo_erro", "reenviado_por", "ultimo_reenvio_em", "atualizado_em"])
        raise ValueError(email_log.ultimo_erro)

    status_anterior = email_log.status
    try:
        email = EmailMultiAlternatives(
            subject=email_log.assunto,
            body=email_log.corpo,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=destinatarios,
        )
        enviados = email.send(fail_silently=False)
        if not enviados:
            raise RuntimeError("Backend SMTP não confirmou envio.")
    except Exception as exc:
        email_log.status = StatusEmailLog.FALHA
        email_log.tentativas = (email_log.tentativas or 0) + 1
        email_log.ultimo_erro = str(exc)[:4000]
        email_log.reenviado_por = usuario if getattr(usuario, "pk", None) else None
        email_log.ultimo_reenvio_em = timezone.now()
        email_log.save(update_fields=["status", "tentativas", "ultimo_erro", "reenviado_por", "ultimo_reenvio_em", "atualizado_em"])
        logger.error(
            "email_reenvio_falha usuario=%s email_log=%s status_anterior=%s erro=%s",
            getattr(usuario, "pk", None),
            email_log.pk,
            status_anterior,
            exc,
        )
        raise

    email_log.status = StatusEmailLog.ENVIADO
    email_log.tentativas = (email_log.tentativas or 0) + 1
    email_log.ultimo_erro = ""
    email_log.enviado_em = timezone.now()
    email_log.reenviado_por = usuario if getattr(usuario, "pk", None) else None
    email_log.ultimo_reenvio_em = timezone.now()
    email_log.save(update_fields=["status", "tentativas", "ultimo_erro", "enviado_em", "reenviado_por", "ultimo_reenvio_em", "atualizado_em"])
    logger.info(
        "email_reenvio_sucesso usuario=%s email_log=%s status_anterior=%s status_novo=%s relatorio=%s",
        getattr(usuario, "pk", None),
        email_log.pk,
        status_anterior,
        email_log.status,
        email_log.relatorio_id,
    )
    return email_log


def reenviar_emails(ids, usuario=None, limite=20):
    resultado = ResultadoReenvio(mensagens=[])
    ids = [int(item) for item in ids if str(item).isdigit()]
    if len(ids) > limite:
        ids = ids[:limite]
        resultado.mensagens.append(f"Limite de {limite} e-mails por execução aplicado.")

    emails = EmailLog.objects.filter(pk__in=ids).order_by("criado_em")
    for email_log in emails:
        if email_log.status not in {StatusEmailLog.PENDENTE, StatusEmailLog.FALHA}:
            resultado.ignorados += 1
            continue
        try:
            reenviar_email_log(email_log, usuario=usuario)
        except Exception as exc:
            resultado.falhas += 1
            resultado.mensagens.append(f"#{email_log.pk}: {exc}")
        else:
            resultado.enviados += 1
    return resultado


def enviar_email_teste(destinatario, usuario=None):
    destinatario = str(destinatario or "").strip()
    try:
        validate_email(destinatario)
    except ValidationError as exc:
        raise ValueError("Informe um destinatário de e-mail válido.") from exc

    assunto = "[Sistema de Reembolso] Teste de e-mail"
    corpo = "\n".join(
        [
            "Este e-mail confirma que a configuração SMTP do sistema está funcionando.",
            "",
            f"Data/hora: {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S')}",
            f"Solicitado por: {getattr(usuario, 'username', 'sistema')}",
            "",
            "Nenhuma senha, token ou dado sensível foi incluído nesta mensagem.",
        ]
    )
    log = EmailLog.objects.create(
        tipo="teste_smtp_manutencao",
        destinatarios=[destinatario],
        assunto=assunto,
        corpo=corpo,
        status=StatusEmailLog.PENDENTE,
    )
    return reenviar_email_log(log, usuario=usuario)
