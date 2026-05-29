from django.core.management.base import BaseCommand, CommandError

from relatorios.models import Cliente, normalizar_texto_busca
from relatorios.services.clientes_api_service import ClientesApiError, buscar_clientes_api
from relatorios.services.clientes_sync_service import _somente_digitos, normalizar_cliente_api


class Command(BaseCommand):
    help = "Compara clientes locais com a API ControlSul para apoiar saneamento/importacao legado."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Limita registros da API processados.")
        parser.add_argument(
            "--mostrar-sem-correspondencia",
            action="store_true",
            help="Lista clientes locais sem correspondencia provavel.",
        )

    def handle(self, *args, **options):
        try:
            registros = buscar_clientes_api()
        except ClientesApiError as exc:
            raise CommandError(f"Comparacao nao realizada: {exc}") from exc

        if options["limit"]:
            registros = registros[: options["limit"]]

        api_por_cnpj = {}
        api_por_nome = {}
        erros = 0
        for payload in registros:
            try:
                dados = normalizar_cliente_api(payload)
            except Exception:
                erros += 1
                continue
            api_por_cnpj[dados["cnpj_cpf"]] = dados
            chave_nome = normalizar_texto_busca(
                dados.get("nome_fantasia") or dados.get("razao_social") or dados.get("nome")
            )
            if chave_nome:
                api_por_nome.setdefault(chave_nome, []).append(dados)

        vinculados_cnpj = 0
        possiveis_nome = 0
        sem_correspondencia = []
        for cliente in Cliente.objects.all().iterator():
            cnpj = _somente_digitos(cliente.cnpj_cpf)
            if cnpj and cnpj in api_por_cnpj:
                vinculados_cnpj += 1
                continue
            chave_nome = normalizar_texto_busca(
                cliente.nome_fantasia or cliente.razao_social or cliente.nome
            )
            candidatos = api_por_nome.get(chave_nome) or []
            if len(candidatos) == 1:
                possiveis_nome += 1
            else:
                sem_correspondencia.append(cliente)

        self.stdout.write(
            self.style.SUCCESS(
                "Comparacao concluida: "
                f"api={len(registros)}, "
                f"vinculos_por_cnpj={vinculados_cnpj}, "
                f"possiveis_por_nome={possiveis_nome}, "
                f"sem_correspondencia={len(sem_correspondencia)}, "
                f"erros_api={erros}"
            )
        )
        if options["mostrar_sem_correspondencia"]:
            for cliente in sem_correspondencia[:50]:
                self.stdout.write(f"- #{cliente.pk} {cliente.nome_exibicao} ({cliente.cnpj_cpf or 'sem CNPJ'})")
            if len(sem_correspondencia) > 50:
                self.stdout.write(f"... mais {len(sem_correspondencia) - 50} cliente(s).")
