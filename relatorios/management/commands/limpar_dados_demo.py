import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from relatorios.models import (
    AnexoRelatorio,
    DespesaRateio,
    HistoricoRelatorio,
    ItemDespesa,
    RelatorioCliente,
    RelatorioSnapshotFinanceiro,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    TrechoKm,
    TrechoRateioKM,
)


logger = logging.getLogger("relatorios.limpeza_demo")
PREFIXO_DEMO = "DEMO-"


class Command(BaseCommand):
    help = "Remove somente relatórios e vínculos explicitamente marcados como DEMO."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria removido sem apagar.")
        parser.add_argument("--confirmar", action="store_true", help="Executa a remoção dos relatórios DEMO.")
        parser.add_argument(
            "--remover-arquivos",
            action="store_true",
            help="Reservado para remoção física futura. Por padrão arquivos físicos não são apagados.",
        )

    def handle(self, *args, **options):
        if options["confirmar"] and options["dry_run"]:
            raise CommandError("Use apenas um: --dry-run ou --confirmar.")
        if not options["confirmar"]:
            options["dry_run"] = True
        if options["remover_arquivos"]:
            raise CommandError("Remoção física de arquivos não foi implementada por segurança.")

        relatorios = RelatorioTecnico.objects.filter(numero__startswith=PREFIXO_DEMO)
        ids = list(relatorios.values_list("pk", flat=True))
        numeros = list(relatorios.values_list("numero", flat=True))
        resumo = self._contar(ids)

        self.stdout.write(self.style.WARNING("Limpeza segura de dados DEMO"))
        self.stdout.write(f"Relatórios DEMO encontrados: {resumo['relatorios']}")
        self.stdout.write(f"Números: {', '.join(numeros) if numeros else '-'}")
        self.stdout.write(f"Despesas DEMO: {resumo['despesas']}")
        self.stdout.write(f"Trechos KM DEMO: {resumo['trechos']}")
        self.stdout.write(f"Rateios de despesa DEMO: {resumo['rateios_despesa']}")
        self.stdout.write(f"Rateios KM DEMO: {resumo['rateios_km']}")
        self.stdout.write(f"Históricos DEMO: {resumo['historicos']}")
        self.stdout.write(f"Snapshots DEMO: {resumo['snapshots']}")
        self.stdout.write(f"Anexos DEMO: {resumo['anexos']}")
        self.stdout.write(self.style.WARNING("Clientes, técnicos e usuários reais não serão removidos."))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run concluído. Nada foi apagado. Use --confirmar para executar."))
            return

        logger.info("Iniciando limpeza DEMO relatorios=%s", numeros)
        with transaction.atomic():
            removidos, _detalhes = relatorios.delete()
        logger.info("Limpeza DEMO finalizada removidos=%s numeros=%s", removidos, numeros)
        self.stdout.write(self.style.SUCCESS(f"Limpeza concluída. Objetos removidos: {removidos}"))

    def _contar(self, ids):
        despesas = ItemDespesa.objects.filter(relatorio_id__in=ids)
        trechos = TrechoKm.objects.filter(relatorio_id__in=ids)
        return {
            "relatorios": len(ids),
            "despesas": despesas.count(),
            "trechos": trechos.count(),
            "rateios_despesa": DespesaRateio.objects.filter(despesa__relatorio_id__in=ids).count(),
            "rateios_km": TrechoRateioKM.objects.filter(trecho__relatorio_id__in=ids).count(),
            "historicos": HistoricoRelatorio.objects.filter(relatorio_id__in=ids).count(),
            "snapshots": RelatorioSnapshotFinanceiro.objects.filter(relatorio_id__in=ids).count(),
            "anexos": AnexoRelatorio.objects.filter(relatorio_id__in=ids).count(),
            "clientes_vinculados": RelatorioCliente.objects.filter(relatorio_id__in=ids).count(),
            "equipe": RelatorioTecnicoEquipe.objects.filter(relatorio_id__in=ids).count(),
        }
