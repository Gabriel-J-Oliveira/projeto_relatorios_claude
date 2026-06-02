from django.core.management.base import BaseCommand, CommandError

from relatorios.services.ad_users_service import sincronizar_usuarios_ad


class Command(BaseCommand):
    help = "Sincroniza técnicos/usuários locais a partir da OU padrão do Active Directory."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Busca no AD e mostra resumo sem gravar.")
        parser.add_argument("--confirmar", action="store_true", help="Grava criação/atualização dos técnicos locais.")
        parser.add_argument("--limite", type=int, default=None, help="Limita a quantidade de usuários processados.")
        parser.add_argument("--verbose", action="store_true", help="Registra detalhes adicionais no log.")

    def handle(self, *args, **options):
        if options["dry_run"] and options["confirmar"]:
            raise CommandError("Use apenas um: --dry-run ou --confirmar.")
        dry_run = not options["confirmar"]
        try:
            resultado = sincronizar_usuarios_ad(
                dry_run=dry_run,
                limit=options["limite"],
                verbose=options["verbose"],
            )
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.WARNING("Sincronização de usuários AD"))
        self.stdout.write(f"Dry-run: {resultado.dry_run}")
        self.stdout.write(f"Usuários encontrados no AD: {resultado.encontrados}")
        self.stdout.write(f"Criados localmente: {resultado.criados}")
        self.stdout.write(f"Atualizados: {resultado.atualizados}")
        self.stdout.write(f"Sem alteração: {resultado.sem_alteracao}")
        self.stdout.write(f"Ignorados: {resultado.ignorados}")
        self.stdout.write(f"Erros: {resultado.erros}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Nada foi gravado. Use --confirmar para aplicar."))
        elif resultado.erros:
            self.stdout.write(self.style.WARNING("Sincronização concluída com erros. Consulte os logs."))
        else:
            self.stdout.write(self.style.SUCCESS("Sincronização concluída com sucesso."))
