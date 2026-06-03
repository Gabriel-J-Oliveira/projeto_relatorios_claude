from django.core.management.base import BaseCommand, CommandError
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.utils import timezone

from relatorios.models import EmailLog, StatusEmailLog


class Command(BaseCommand):
    help = "Reenvia e-mails internos pendentes ou com falha registrados no EmailLog."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Lista sem enviar.")
        parser.add_argument("--confirmar", action="store_true", help="Executa o reenvio.")
        parser.add_argument("--relatorio", help="Filtra por ID ou numero do relatorio.")
        parser.add_argument("--tipo", help="Filtra pelo tipo do e-mail.")
        parser.add_argument("--limite", type=int, help="Limita a quantidade processada.")

    def handle(self, *args, **options):
        confirmar = options["confirmar"]
        if not confirmar:
            options["dry_run"] = True

        qs = EmailLog.objects.filter(
            status__in=[StatusEmailLog.PENDENTE, StatusEmailLog.FALHA]
        ).order_by("criado_em")

        if options.get("tipo"):
            qs = qs.filter(tipo=options["tipo"])

        relatorio_ref = options.get("relatorio")
        if relatorio_ref:
            filtro = Q(relatorio__numero=relatorio_ref)
            if str(relatorio_ref).isdigit():
                filtro |= Q(relatorio_id=int(relatorio_ref))
            qs = qs.filter(filtro)

        limite = options.get("limite")
        if limite:
            qs = qs[:limite]

        emails = list(qs)
        self.stdout.write(f"{len(emails)} e-mail(s) pendente(s)/falho(s) encontrado(s).")
        for log in emails:
            self.stdout.write(
                f"- id={log.pk} tipo={log.tipo} relatorio={log.relatorio_id or '-'} "
                f"status={log.status} destinatarios={', '.join(log.destinatarios or [])}"
            )

        if not confirmar:
            self.stdout.write(self.style.WARNING("Dry-run: nenhum e-mail foi reenviado. Use --confirmar para executar."))
            return

        enviados = 0
        falhas = 0
        for log in emails:
            destinatarios = [email for email in (log.destinatarios or []) if email]
            if not destinatarios:
                log.status = StatusEmailLog.FALHA
                log.tentativas = (log.tentativas or 0) + 1
                log.ultimo_erro = "Sem destinatarios validos para reenvio."
                log.save(update_fields=["status", "tentativas", "ultimo_erro", "atualizado_em"])
                falhas += 1
                continue

            try:
                email = EmailMultiAlternatives(
                    subject=log.assunto,
                    body=log.corpo,
                    to=destinatarios,
                )
                email.send(fail_silently=False)
            except Exception as exc:
                log.status = StatusEmailLog.FALHA
                log.tentativas = (log.tentativas or 0) + 1
                log.ultimo_erro = str(exc)[:4000]
                log.save(update_fields=["status", "tentativas", "ultimo_erro", "atualizado_em"])
                falhas += 1
                continue

            log.status = StatusEmailLog.ENVIADO
            log.tentativas = (log.tentativas or 0) + 1
            log.ultimo_erro = ""
            log.enviado_em = timezone.now()
            log.save(update_fields=["status", "tentativas", "ultimo_erro", "enviado_em", "atualizado_em"])
            enviados += 1

        if falhas:
            raise CommandError(f"Reenvio concluido com falhas. enviados={enviados} falhas={falhas}")
        self.stdout.write(self.style.SUCCESS(f"Reenvio concluido. enviados={enviados} falhas={falhas}"))
