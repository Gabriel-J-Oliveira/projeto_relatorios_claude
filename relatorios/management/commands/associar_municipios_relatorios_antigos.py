import logging

from django.core.management.base import BaseCommand

from relatorios.models import Municipio, RelatorioTecnico, normalizar_texto_busca


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Associa relatórios antigos ao cadastro normalizado de municípios quando houver correspondência única."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Simula a associação sem gravar.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        associados = nao_encontrados = ambiguos = 0
        qs = RelatorioTecnico.objects.filter(municipio_atendimento__isnull=True).only(
            "pk",
            "cidade_atendimento",
            "uf_atendimento",
        )
        for relatorio in qs.iterator():
            cidade_norm = normalizar_texto_busca(relatorio.cidade_atendimento)
            uf = (relatorio.uf_atendimento or "").strip().upper()
            candidatos = Municipio.objects.filter(
                ativo=True,
                nome_normalizado=cidade_norm,
                uf=uf,
            )
            total = candidatos.count()
            if total == 1:
                municipio = candidatos.first()
                associados += 1
                if not dry_run:
                    relatorio.municipio_atendimento = municipio
                    relatorio.sincronizar_municipio_atendimento()
                    relatorio.save(
                        update_fields=[
                            "municipio_atendimento",
                            "cidade_atendimento",
                            "uf_atendimento",
                            "tipo_localidade",
                            "cidade_atendimento_normalizada",
                            "uf_atendimento_normalizada",
                            "tipo_localidade_calculada",
                            "atualizado_em",
                        ]
                    )
            elif total > 1:
                ambiguos += 1
                logger.warning("Município ambíguo para relatório %s: %s/%s", relatorio.pk, relatorio.cidade_atendimento, uf)
            else:
                nao_encontrados += 1
                logger.warning("Município não encontrado para relatório %s: %s/%s", relatorio.pk, relatorio.cidade_atendimento, uf)

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill concluído: associados={associados} | ambiguos={ambiguos} | "
                f"nao_encontrados={nao_encontrados} | dry_run={dry_run}"
            )
        )
