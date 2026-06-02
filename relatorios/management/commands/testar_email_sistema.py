import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.utils import timezone


logger = logging.getLogger("relatorios.services.email_service")


class Command(BaseCommand):
    help = "Envia um e-mail simples de teste usando a configuracao SMTP do sistema."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="E-mail que recebera a mensagem de teste.")

    def handle(self, *args, **options):
        destinatario = (options["destinatario"] or "").strip()
        try:
            validate_email(destinatario)
        except ValidationError as exc:
            raise CommandError("Informe um destinatario de e-mail valido.") from exc

        remetente = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
        if not remetente:
            raise CommandError("Configure DEFAULT_FROM_EMAIL antes de testar o envio.")

        backend = getattr(settings, "EMAIL_BACKEND", "")
        host = getattr(settings, "EMAIL_HOST", "")
        porta = getattr(settings, "EMAIL_PORT", "")

        logger.info(
            "email_teste_inicio destinatarios=%s backend=%s host=%s porta=%s",
            [destinatario],
            backend,
            host,
            porta,
        )

        assunto = "[Sistema de Reembolso] Teste de e-mail"
        corpo = "\n".join(
            [
                "Este e-mail confirma que a configuracao SMTP do sistema esta funcionando.",
                "",
                f"Data/hora: {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S')}",
                f"Backend: {backend}",
                f"Host SMTP: {host}",
                "",
                "Nenhuma senha, token ou dado sensivel foi incluido nesta mensagem.",
            ]
        )

        try:
            email = EmailMultiAlternatives(
                subject=assunto,
                body=corpo,
                from_email=remetente,
                to=[destinatario],
            )
            enviados = email.send(fail_silently=False)
        except Exception as exc:
            logger.exception(
                "email_teste_falha destinatarios=%s backend=%s host=%s porta=%s",
                [destinatario],
                backend,
                host,
                porta,
            )
            raise CommandError(f"Falha ao enviar e-mail de teste: {exc}") from exc

        if enviados != 1:
            logger.warning(
                "email_teste_sem_confirmacao destinatarios=%s enviados=%s",
                [destinatario],
                enviados,
            )
            raise CommandError("O backend nao confirmou o envio do e-mail de teste.")

        logger.info("email_teste_sucesso destinatarios=%s", [destinatario])
        self.stdout.write(self.style.SUCCESS(f"E-mail de teste enviado para {destinatario}."))
