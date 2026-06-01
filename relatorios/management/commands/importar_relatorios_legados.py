from django.core.management.base import BaseCommand, CommandError

from relatorios.services.relatorios_legados_importacao import importar_relatorios_legados_csv


class Command(BaseCommand):
    help = "Importa relatórios históricos frios da planilha antiga."

    def add_arguments(self, parser):
        parser.add_argument("arquivo_csv", help="Caminho do CSV exportado da planilha antiga.")
        parser.add_argument("--dry-run", action="store_true", help="Lê e valida sem gravar.")
        parser.add_argument("--confirmar", action="store_true", help="Grava a importação.")
        parser.add_argument("--limite", type=int, default=None, help="Limita a quantidade de linhas lidas.")
        parser.add_argument("--substituir", action="store_true", help="Atualiza relatórios legados já importados.")
        parser.add_argument("--usuario", default="", help="Username registrado como importador, se existir.")

    def handle(self, *args, **options):
        if options["confirmar"] and options["dry_run"]:
            raise CommandError("Use apenas um: --dry-run ou --confirmar.")
        if not options["confirmar"]:
            options["dry_run"] = True

        resultado = importar_relatorios_legados_csv(
            options["arquivo_csv"],
            confirmar=options["confirmar"],
            usuario=options["usuario"],
            limite=options["limite"],
            substituir=options["substituir"],
        )

        modo = "DRY-RUN" if options["dry_run"] else "IMPORTAÇÃO"
        self.stdout.write(self.style.WARNING(f"{modo} de relatórios legados"))
        self.stdout.write(f"Linhas lidas: {resultado.lidos}")
        self.stdout.write(f"Linhas vazias/sem dados ignoradas: {resultado.ignorados_vazios}")
        self.stdout.write(f"Criados: {resultado.criados}")
        self.stdout.write(f"Atualizados: {resultado.atualizados}")
        self.stdout.write(f"Sem alteração: {resultado.sem_alteracao}")
        self.stdout.write(f"Despesas identificadas: {resultado.despesas}")
        self.stdout.write(f"Blocos de KM identificados: {resultado.kms}")
        self.stdout.write(f"Pendências de vínculo cliente/técnico: {resultado.pendencias}")
        self.stdout.write(f"Erros: {resultado.erros}")

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    "Nada foi gravado. Execute novamente com --confirmar para importar."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Relatórios legados importados como histórico frio."))
