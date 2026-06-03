from django.core.management.base import BaseCommand, CommandError

from relatorios.models import RelatorioTecnico
from relatorios.services.email_service import (
    get_financeiro_recipients,
    reenviar_relatorio_financeiro_manual,
)


class Command(BaseCommand):
    help = "Reenvia avisos financeiros de relatorios somente para a caixa central do financeiro."

    def add_arguments(self, parser):
        parser.add_argument(
            "--relatorios",
            required=True,
            help="Lista de IDs ou numeros de relatorio separada por virgula.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Lista sem enviar.")
        parser.add_argument("--confirmar", action="store_true", help="Executa o envio.")
        parser.add_argument("--limite", type=int, help="Limita a quantidade processada.")

    def _buscar_relatorios(self, refs):
        relatorios = []
        nao_encontrados = []
        for ref in refs:
            ref = ref.strip()
            if not ref:
                continue
            qs = RelatorioTecnico.objects.all()
            relatorio = qs.filter(pk=int(ref)).first() if ref.isdigit() else None
            if relatorio is None:
                relatorio = qs.filter(numero=ref).first()
            if relatorio is None:
                nao_encontrados.append(ref)
                continue
            relatorios.append(relatorio)
        return relatorios, nao_encontrados

    def handle(self, *args, **options):
        confirmar = options["confirmar"]
        refs = [item.strip() for item in str(options["relatorios"] or "").split(",") if item.strip()]
        relatorios, nao_encontrados = self._buscar_relatorios(refs)
        limite = options.get("limite")
        if limite:
            relatorios = relatorios[:limite]

        destinatarios = get_financeiro_recipients()
        self.stdout.write(f"Destinatarios financeiros centrais: {', '.join(destinatarios) or '-'}")
        self.stdout.write(f"{len(relatorios)} relatorio(s) encontrado(s) para reenvio.")
        for relatorio in relatorios:
            self.stdout.write(f"- id={relatorio.pk} numero={relatorio.numero} status={relatorio.status}")
        for ref in nao_encontrados:
            self.stdout.write(self.style.WARNING(f"- nao encontrado: {ref}"))

        if not confirmar:
            self.stdout.write(self.style.WARNING("Dry-run: nenhum e-mail foi enviado. Use --confirmar para executar."))
            return
        if not destinatarios:
            raise CommandError("FINANCEIRO_EMAIL nao possui e-mail valido.")

        enviados = 0
        falhas = 0
        for relatorio in relatorios:
            try:
                reenviar_relatorio_financeiro_manual(relatorio)
            except Exception as exc:
                falhas += 1
                self.stderr.write(self.style.ERROR(f"Falha no relatorio {relatorio.pk}: {exc}"))
                continue
            enviados += 1

        if falhas:
            raise CommandError(f"Reenvio financeiro concluido com falhas. enviados={enviados} falhas={falhas}")
        self.stdout.write(self.style.SUCCESS(f"Reenvio financeiro concluido. enviados={enviados} falhas={falhas}"))
