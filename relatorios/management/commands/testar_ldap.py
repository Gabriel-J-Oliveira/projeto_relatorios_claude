from getpass import getpass

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.management.base import BaseCommand, CommandError

from relatorios.services.identidade.grupo_mapping_service import (
    mapear_grupos_ad_para_django,
    validar_mapeamento_grupos_ad,
)
from relatorios.services.identidade.ldap_utils import (
    construir_snapshot_ldap,
    descrever_grupos_ad,
    extrair_grupos_ad,
    normalizar_username_ad,
    status_conta_ad,
)
from relatorios.services.identidade.sincronizacao_service import (
    sincronizar_usuario_externo,
)


class Command(BaseCommand):
    help = "Testa comunicacao LDAP/AD, busca de usuario, grupos e sincronizacao ERP."

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario",
            help="Usuario AD para buscar usando LDAP_USER_SEARCH_FILTER.",
        )
        parser.add_argument(
            "--senha",
            help="Senha do usuario final para testar bind/autenticacao. Evite em historico de shell.",
        )
        parser.add_argument(
            "--pedir-senha",
            action="store_true",
            help="Solicita a senha do usuario final de forma interativa.",
        )
        parser.add_argument(
            "--testar-bind-usuario",
            action="store_true",
            help="Apos encontrar o usuario, testa bind LDAP direto com o DN dele.",
        )
        parser.add_argument(
            "--autenticar",
            action="store_true",
            help="Testa autenticacao final pelo backend Django, incluindo sincronizacao real.",
        )
        parser.add_argument(
            "--dry-run-sync",
            action="store_true",
            help="Simula sincronizacao do usuario encontrado sem gravar no banco.",
        )
        parser.add_argument(
            "--verbose-sync",
            action="store_true",
            help="Mostra detalhes adicionais da sincronizacao dry-run.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "LDAP_AUTH_ENABLED", False):
            raise CommandError("LDAP_AUTH_ENABLED=False. Ative a flag para testar LDAP.")

        ldap, escape_filter_chars = self._carregar_ldap()
        self._validar_mapeamento()

        usuario = options.get("usuario")
        senha = options.get("senha")
        if options["pedir_senha"]:
            senha = getpass("Senha AD do usuario final: ")

        conexao = self._conectar_e_bind_servico(ldap)

        if not usuario:
            self.stdout.write("Nenhum usuario informado; teste encerrado apos bind de servico.")
            return

        dn, attrs = self._buscar_usuario(conexao, ldap, escape_filter_chars, usuario)
        grupos_ad = tuple(
            sorted(
                set(extrair_grupos_ad(attrs=attrs))
                | set(self._buscar_grupos_aninhados(conexao, ldap, escape_filter_chars, dn))
            )
        )
        grupos_django = mapear_grupos_ad_para_django(grupos_ad)
        snapshot = construir_snapshot_ldap(usuario, attrs, grupos_ad=grupos_ad)

        self._imprimir_usuario_encontrado(dn, attrs, snapshot, grupos_ad, grupos_django)

        if options["testar_bind_usuario"]:
            if not senha:
                raise CommandError("Informe --senha ou use --pedir-senha para testar bind do usuario.")
            self._bind_usuario(ldap, dn, senha, snapshot.username)

        if options["dry_run_sync"]:
            self._dry_run_sync(snapshot, verbose=options["verbose_sync"])

        if options["autenticar"]:
            if not senha:
                raise CommandError("Informe --senha ou use --pedir-senha para testar autenticacao final.")
            self._autenticar_backend_django(usuario, senha)

        self.stdout.write(self.style.SUCCESS("Teste LDAP finalizado."))

    def _carregar_ldap(self):
        try:
            import ldap
            from ldap.filter import escape_filter_chars
        except ImportError as exc:
            raise CommandError(
                "Dependencias LDAP indisponiveis. Instale django-auth-ldap/python-ldap."
            ) from exc
        return ldap, escape_filter_chars

    def _validar_mapeamento(self):
        validacao = validar_mapeamento_grupos_ad()
        if not validacao["valido"]:
            raise CommandError(
                "AD_GROUP_MAPPING possui grupos Django invalidos: "
                + ", ".join(validacao["grupos_invalidos"])
            )
        self.stdout.write(self.style.SUCCESS("AD_GROUP_MAPPING valido."))

    def _uris_ldap(self):
        return list(getattr(settings, "LDAP_SERVER_URIS", None) or [settings.AUTH_LDAP_SERVER_URI])

    def _abrir_conexao_ldap(self, ldap, uri):
        for opcao, valor in getattr(settings, "AUTH_LDAP_GLOBAL_OPTIONS", {}).items():
            ldap.set_option(opcao, valor)
        conexao = ldap.initialize(uri)
        for opcao, valor in getattr(settings, "AUTH_LDAP_CONNECTION_OPTIONS", {}).items():
            conexao.set_option(opcao, valor)
        return conexao

    def _conectar_e_bind_servico(self, ldap):
        uris = list(getattr(settings, "LDAP_SERVER_URIS", None) or [settings.AUTH_LDAP_SERVER_URI])
        self.stdout.write("DCs configurados: " + ", ".join(uris))
        ultimo_erro = None
        for uri in uris:
            self.stdout.write(f"Conectando em {uri}...")
            try:
                conexao = self._abrir_conexao_ldap(ldap, uri)
                self._bind_servico(conexao, ldap)
                return conexao
            except ldap.LDAPError as exc:
                ultimo_erro = exc
                self.stdout.write(self.style.WARNING(f"Falha em {uri}: {exc.__class__.__name__}"))
                continue
        raise CommandError(f"Nenhum DC LDAP respondeu ao bind de servico: {ultimo_erro}")

    def _bind_servico(self, conexao, ldap):
        try:
            if getattr(settings, "AUTH_LDAP_START_TLS", False):
                conexao.start_tls_s()
            conexao.simple_bind_s(
                getattr(settings, "AUTH_LDAP_BIND_DN", ""),
                getattr(settings, "AUTH_LDAP_BIND_PASSWORD", ""),
            )
        except ldap.INVALID_CREDENTIALS as exc:
            raise CommandError("Falha no bind LDAP de servico: credenciais invalidas.") from exc
        except ldap.TIMEOUT as exc:
            raise CommandError("Timeout no bind LDAP de servico.") from exc
        except ldap.SERVER_DOWN as exc:
            raise CommandError("Servidor LDAP/DC indisponivel no bind de servico.") from exc
        except ldap.LDAPError as exc:
            raise CommandError(f"Falha no bind LDAP de servico: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Bind LDAP de servico realizado com sucesso."))

    def _buscar_usuario(self, conexao, ldap, escape_filter_chars, usuario):
        filtro = settings.LDAP_USER_SEARCH_FILTER % {
            "user": escape_filter_chars(normalizar_username_ad(usuario))
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
            "userAccountControl",
            "lockoutTime",
            "accountExpires",
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
        except ldap.TIMEOUT as exc:
            raise CommandError("Timeout na busca LDAP de usuario.") from exc
        except ldap.SERVER_DOWN as exc:
            raise CommandError("Servidor LDAP/DC indisponivel na busca de usuario.") from exc
        except ldap.LDAPError as exc:
            raise CommandError(f"Falha na busca LDAP: {exc}") from exc

        resultados = [
            (dn, attrs)
            for dn, attrs in resultados
            if dn and isinstance(attrs, dict)
        ]
        if not resultados:
            raise CommandError("Usuario nao encontrado no LDAP.")

        if len(resultados) > 1:
            self.stdout.write(
                self.style.WARNING(
                    f"A busca retornou {len(resultados)} usuarios; usando o primeiro resultado."
                )
            )
        return resultados[0]

    def _buscar_grupos_aninhados(self, conexao, ldap, escape_filter_chars, user_dn):
        base_grupos = getattr(settings, "LDAP_GROUP_SEARCH_BASE_DN", "")
        if not base_grupos:
            return ()

        user_dn_seguro = escape_filter_chars(user_dn)
        filtro = f"(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={user_dn_seguro}))"
        try:
            resultados = conexao.search_s(
                base_grupos,
                ldap.SCOPE_SUBTREE,
                filtro,
                ["distinguishedName", "cn"],
            )
        except ldap.LDAPError:
            return ()

        grupos = []
        for dn, attrs in resultados:
            if dn:
                grupos.append(dn)
        return tuple(grupos)

    def _imprimir_usuario_encontrado(self, dn, attrs, snapshot, grupos_ad, grupos_django):
        self.stdout.write(self.style.SUCCESS(f"Usuario encontrado: {dn}"))
        self.stdout.write(f"Username Django: {snapshot.username}")
        self.stdout.write(f"Email: {snapshot.email or '-'}")
        self.stdout.write(f"Nome: {(snapshot.first_name + ' ' + snapshot.last_name).strip() or '-'}")
        status = status_conta_ad(attrs)
        self.stdout.write(
            "Status AD: "
            + (", ".join(status) if status else "ativo")
        )
        self.stdout.write(f"Grupos AD encontrados: {len(grupos_ad)}")
        if grupos_ad:
            for grupo in descrever_grupos_ad(grupos_ad):
                self.stdout.write(f"  - {grupo}")
        else:
            self.stdout.write(self.style.WARNING("  - Nenhum grupo AD encontrado em memberOf."))
        self.stdout.write(
            "Grupos ERP mapeados: "
            + (", ".join(grupos_django) if grupos_django else "-")
        )
        if not grupos_django:
            self.stdout.write(
                self.style.WARNING(
                    "Nenhum grupo ERP mapeado. O usuario pode autenticar, mas ficar sem permissoes de negocio."
                )
            )

    def _bind_usuario(self, ldap, dn, senha, username):
        ultimo_erro = None
        for uri in self._uris_ldap():
            try:
                conexao_usuario = self._abrir_conexao_ldap(ldap, uri)
                if getattr(settings, "AUTH_LDAP_START_TLS", False):
                    conexao_usuario.start_tls_s()
                conexao_usuario.simple_bind_s(dn, senha)
                self.stdout.write(self.style.SUCCESS(f"Bind do usuario {username} realizado com sucesso em {uri}."))
                return
            except ldap.INVALID_CREDENTIALS as exc:
                raise CommandError(f"Bind do usuario {username} recusado: senha invalida.") from exc
            except (ldap.TIMEOUT, ldap.SERVER_DOWN) as exc:
                ultimo_erro = exc
                self.stdout.write(self.style.WARNING(f"Falha no bind do usuario em {uri}: {exc.__class__.__name__}"))
                continue
            except ldap.LDAPError as exc:
                ultimo_erro = exc
                self.stdout.write(self.style.WARNING(f"Falha no bind do usuario em {uri}: {exc.__class__.__name__}"))
                continue
        raise CommandError(f"Falha no bind do usuario {username} em todos os DCs: {ultimo_erro}")

    def _dry_run_sync(self, snapshot, verbose=False):
        resultado = sincronizar_usuario_externo(snapshot, dry_run=True)
        self.stdout.write(
            self.style.SUCCESS(
                "Dry-run sincronizacao: "
                f"criado={resultado.criado}, atualizado={resultado.atualizado}, "
                f"adicionados={resultado.grupos_adicionados}, "
                f"removidos={resultado.grupos_removidos}"
            )
        )
        if verbose:
            self.stdout.write(f"  grupos_django={resultado.grupos_django}")
            self.stdout.write(f"  grupos_adicionados={resultado.grupos_adicionados}")
            self.stdout.write(f"  grupos_removidos={resultado.grupos_removidos}")

    def _autenticar_backend_django(self, usuario, senha):
        autenticado = authenticate(username=usuario, password=senha)
        if not autenticado:
            raise CommandError("Autenticacao final pelo backend Django falhou.")

        grupos = list(autenticado.groups.values_list("name", flat=True))
        self.stdout.write(
            self.style.SUCCESS(
                f"Autenticacao final OK. Usuario Django: {autenticado.username}. "
                f"Grupos atuais: {', '.join(grupos) if grupos else '-'}"
            )
        )
        User = get_user_model()
        user_db = User.objects.get(pk=autenticado.pk)
        if user_db.has_usable_password():
            self.stdout.write(
                self.style.WARNING(
                    "Usuario autenticado via AD ainda possui senha local utilizavel."
                )
            )
