from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from relatorios.services.identidade.grupo_mapping_service import (
    validar_mapeamento_grupos_ad,
)


class Command(BaseCommand):
    help = "Healthcheck rapido dos Domain Controllers LDAP configurados."

    def handle(self, *args, **options):
        if not getattr(settings, "LDAP_AUTH_ENABLED", False):
            raise CommandError("LDAP_AUTH_ENABLED=False. Healthcheck LDAP desativado.")

        try:
            import ldap
        except ImportError as exc:
            raise CommandError("Dependencias LDAP indisponiveis.") from exc

        validacao = validar_mapeamento_grupos_ad()
        if not validacao["valido"]:
            raise CommandError(
                "AD_GROUP_MAPPING invalido: "
                + ", ".join(validacao["grupos_invalidos"])
            )

        uris = list(getattr(settings, "LDAP_SERVER_URIS", None) or [settings.AUTH_LDAP_SERVER_URI])
        if not uris:
            raise CommandError("Nenhum DC LDAP configurado.")

        falhas = []
        for uri in uris:
            try:
                for opcao, valor in getattr(settings, "AUTH_LDAP_GLOBAL_OPTIONS", {}).items():
                    ldap.set_option(opcao, valor)
                conexao = ldap.initialize(uri)
                for opcao, valor in getattr(settings, "AUTH_LDAP_CONNECTION_OPTIONS", {}).items():
                    conexao.set_option(opcao, valor)
                if getattr(settings, "AUTH_LDAP_START_TLS", False):
                    conexao.start_tls_s()
                conexao.simple_bind_s(
                    getattr(settings, "AUTH_LDAP_BIND_DN", ""),
                    getattr(settings, "AUTH_LDAP_BIND_PASSWORD", ""),
                )
                self.stdout.write(self.style.SUCCESS(f"OK {uri}"))
            except ldap.INVALID_CREDENTIALS:
                falhas.append((uri, "credenciais do bind invalidas"))
                self.stdout.write(self.style.ERROR(f"FALHA {uri}: credenciais do bind invalidas"))
            except ldap.TIMEOUT:
                falhas.append((uri, "timeout"))
                self.stdout.write(self.style.ERROR(f"FALHA {uri}: timeout"))
            except ldap.SERVER_DOWN:
                falhas.append((uri, "DC indisponivel"))
                self.stdout.write(self.style.ERROR(f"FALHA {uri}: DC indisponivel"))
            except ldap.LDAPError as exc:
                falhas.append((uri, exc.__class__.__name__))
                self.stdout.write(self.style.ERROR(f"FALHA {uri}: {exc.__class__.__name__}"))

        if len(falhas) == len(uris):
            raise CommandError("Todos os DCs LDAP falharam.")

        if falhas:
            self.stdout.write(self.style.WARNING("Healthcheck LDAP parcial: ha DCs com falha."))
        else:
            self.stdout.write(self.style.SUCCESS("Healthcheck LDAP OK em todos os DCs."))
