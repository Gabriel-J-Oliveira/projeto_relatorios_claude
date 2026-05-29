from django.core.management.base import BaseCommand, CommandError

from relatorios.services.clientes_api_service import ClientesApiError
from relatorios.services.clientes_sync_service import sincronizar_clientes


class Command(BaseCommand):
    help = "Sincroniza a base local de clientes com a API externa da ControlSul."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Busca e processa os dados sem gravar alteracoes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limita a quantidade de clientes processados.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Forca atualizacao mesmo quando o hash indica que nada mudou.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Registra detalhes por cliente no logger.",
        )

    def handle(self, *args, **options):
        try:
            resultado = sincronizar_clientes(
                dry_run=options["dry_run"],
                limit=options["limit"],
                force=options["force"],
                verbose=options["verbose"],
            )
        except ClientesApiError as exc:
            raise CommandError(f"Sincronizacao nao realizada: {exc}") from exc

        estilo = self.style.WARNING if options["dry_run"] else self.style.SUCCESS
        self.stdout.write(
            estilo(
                "Clientes processados: "
                f"recebidos={resultado.total_recebidos}, "
                f"criados={resultado.criados}, "
                f"criados_sem_valor_km={resultado.criados_sem_valor_km}, "
                f"atualizados={resultado.atualizados}, "
                f"sem_alteracao={resultado.sem_alteracao}, "
                f"inativados={resultado.inativados}, "
                f"pendentes_valor_km={resultado.pendentes_valor_km}, "
                f"erros={resultado.erros}"
            )
        )

        if resultado.detalhes_erros:
            self.stdout.write(self.style.WARNING("Erros por cliente:"))
            for detalhe in resultado.detalhes_erros[:20]:
                self.stdout.write(f"- {detalhe}")
            if len(resultado.detalhes_erros) > 20:
                self.stdout.write(f"... mais {len(resultado.detalhes_erros) - 20} erro(s).")
