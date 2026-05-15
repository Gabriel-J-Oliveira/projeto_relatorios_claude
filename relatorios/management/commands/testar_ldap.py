from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from relatorios.services.identidade.grupo_mapping_service import (
    mapear_grupos_ad_para_django,
    validar_mapeamento_grupos_ad,
)
from relatorios.services.identidade.ldap_utils import (
    construir_snapshot_ldap,
    descrever_grupos_ad,
    extrair_grupos_ad,
)
from relatorios.services.identidade.sincronizacao_service import (
    sincronizar_usuario_externo,
)


class Command(BaseCommand):
    help = "Testa bind/busca LDAP e valida o mapeamento de grupos AD para grupos ERP."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario",
            help="Usuario AD para buscar usando LDAP_USER_SEARCH_FILTER.",
        )
        parser.add_argument(
            "--dry-run-sync",
            action="store_true",
            help="Simula sincronizacao do usuario encontrado sem gravar no banco.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "LDAP_AUTH_ENABLED", False):
            raise CommandError("LDAP_AUTH_ENABLED=False. Ative a flag para testar LDAP.")

        try:
            import ldap
            from ldap.filter import escape_filter_chars
        except ImportError as exc:
            raise CommandError(
                "Dependencias LDAP indisponiveis. Instale django-auth-ldap/python-ldap."
            ) from exc

        validacao = validar_mapeamento_grupos_ad()
        if not validacao["valido"]:
            raise CommandError(
                "AD_GROUP_MAPPING possui grupos Django invalidos: "
                + ", ".join(validacao["grupos_invalidos"])
            )

        conexao = ldap.initialize(settings.AUTH_LDAP_SERVER_URI)
        for opcao, valor in getattr(settings, "AUTH_LDAP_CONNECTION_OPTIONS", {}).items():
            conexao.set_option(opcao, valor)

        self.stdout.write(f"Conectando em {settings.AUTH_LDAP_SERVER_URI}...")
        try:
            if getattr(settings, "AUTH_LDAP_START_TLS", False):
                conexao.start_tls_s()
            conexao.simple_bind_s(
                getattr(settings, "AUTH_LDAP_BIND_DN", ""),
                getattr(settings, "AUTH_LDAP_BIND_PASSWORD", ""),
            )
        except ldap.LDAPError as exc:
            raise CommandError(f"Falha no bind LDAP: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Bind LDAP realizado com sucesso."))

        usuario = options.get("usuario")
        if not usuario:
            self.stdout.write("Nenhum usuario informado; teste encerrado apos bind.")
            return

        filtro = settings.LDAP_USER_SEARCH_FILTER % {
            "user": escape_filter_chars(usuario)
        }
        atributos = [
            "sAMAccountName",
            "userPrincipalName",
            "givenName",
            "sn",
            "displayName",
            "mail",
            "distinguishedName",
            "memberOf",
        ]
        self.stdout.write(
            f"Buscando usuario em {settings.LDAP_USER_SEARCH_BASE_DN} com filtro {filtro}..."
        )
        try:
            resultados = conexao.search_s(
                settings.LDAP_USER_SEARCH_BASE_DN,
                ldap.SCOPE_SUBTREE,
                filtro,
                atributos,
            )
        except ldap.LDAPError as exc:
            raise CommandError(f"Falha na busca LDAP: {exc}") from exc

        resultados = [
            (dn, attrs)
            for dn, attrs in resultados
            if dn and isinstance(attrs, dict)
        ]
        if not resultados:
            raise CommandError("Usuario nao encontrado no LDAP.")

        dn, attrs = resultados[0]
        grupos_ad = extrair_grupos_ad(attrs=attrs)
        grupos_django = mapear_grupos_ad_para_django(grupos_ad)
        snapshot = construir_snapshot_ldap(usuario, attrs, grupos_ad=grupos_ad)

        self.stdout.write(self.style.SUCCESS(f"Usuario encontrado: {dn}"))
        self.stdout.write(f"Username Django: {snapshot.username}")
        self.stdout.write(f"Email: {snapshot.email or '-'}")
        self.stdout.write(f"Grupos AD encontrados: {len(grupos_ad)}")
        for grupo in descrever_grupos_ad(grupos_ad):
            self.stdout.write(f"  - {grupo}")
        self.stdout.write(
            "Grupos ERP mapeados: "
            + (", ".join(grupos_django) if grupos_django else "-")
        )

        if options["dry_run_sync"]:
            resultado = sincronizar_usuario_externo(snapshot, dry_run=True)
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry-run sincronizacao: "
                    f"criado={resultado.criado}, atualizado={resultado.atualizado}, "
                    f"adicionados={resultado.grupos_adicionados}, "
                    f"removidos={resultado.grupos_removidos}"
                )
            )
