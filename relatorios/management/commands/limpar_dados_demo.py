import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from relatorios.models import (
    AnexoRelatorio,
    DespesaRateio,
    HistoricoRelatorio,
    ItemDespesa,
    RelatorioCliente,
    RelatorioSnapshotFinanceiro,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    Tecnico,
    TrechoKm,
    TrechoRateioKM,
)


logger = logging.getLogger("relatorios.limpeza_demo")
PREFIXO_DEMO = "DEMO-"
PREFIXO_EMAIL_DEMO = "demo."
PREFIXO_NOME_TECNICO_DEMO = "Demo "


class Command(BaseCommand):
    help = "Remove somente relatórios, técnicos e usuários explicitamente marcados como DEMO."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria removido sem apagar.")
        parser.add_argument("--confirmar", action="store_true", help="Executa a remoção dos dados DEMO.")
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
        tecnicos = self._tecnicos_demo()
        usuarios = self._usuarios_demo()
        resumo = self._contar(ids)
        resumo["tecnicos_demo"] = tecnicos.count()
        resumo["usuarios_demo"] = usuarios.count()

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
        self.stdout.write(f"Técnicos DEMO: {resumo['tecnicos_demo']}")
        self.stdout.write(f"Usuários DEMO: {resumo['usuarios_demo']}")
        self.stdout.write(self.style.WARNING("Clientes, técnicos e usuários reais não serão removidos."))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run concluído. Nada foi apagado. Use --confirmar para executar."))
            return

        logger.info(
            "Iniciando limpeza DEMO relatorios=%s tecnicos=%s usuarios=%s",
            numeros,
            list(tecnicos.values_list("email", flat=True)),
            list(usuarios.values_list("username", flat=True)),
        )
        with transaction.atomic():
            removidos_relatorios, _detalhes_relatorios = relatorios.delete()
            removidos_tecnicos, _detalhes_tecnicos = tecnicos.delete()
            removidos_usuarios, _detalhes_usuarios = usuarios.delete()
        removidos = removidos_relatorios + removidos_tecnicos + removidos_usuarios
        logger.info(
            "Limpeza DEMO finalizada removidos=%s numeros=%s tecnicos=%s usuarios=%s",
            removidos,
            numeros,
            removidos_tecnicos,
            removidos_usuarios,
        )
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

    def _tecnicos_demo(self):
        tecnicos_em_relatorios_reais = RelatorioTecnico.objects.exclude(
            numero__startswith=PREFIXO_DEMO
        ).values("tecnico_responsavel_id")
        tecnicos_em_equipes_reais = RelatorioTecnicoEquipe.objects.exclude(
            relatorio__numero__startswith=PREFIXO_DEMO
        ).values("tecnico_id")
        return Tecnico.objects.filter(
            Q(email__istartswith=PREFIXO_EMAIL_DEMO)
            | Q(nome__istartswith=PREFIXO_NOME_TECNICO_DEMO)
        ).exclude(
            pk__in=tecnicos_em_relatorios_reais
        ).exclude(
            pk__in=tecnicos_em_equipes_reais
        )

    def _usuarios_demo(self):
        User = get_user_model()
        return User.objects.filter(
            Q(username__istartswith=PREFIXO_EMAIL_DEMO)
            | Q(email__istartswith=PREFIXO_EMAIL_DEMO)
        )
