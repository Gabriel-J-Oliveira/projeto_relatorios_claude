import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from relatorios.models import Cliente, normalizar_texto_busca
from relatorios.services.clientes_sync_service import _somente_digitos
from relatorios.services.clientes_valor_km_service import normalizar_valor_km


class Command(BaseCommand):
    help = "Importa valor_km local dos clientes a partir de CSV."

    def add_arguments(self, parser):
        parser.add_argument("arquivo_csv", help="CSV com cnpj_cpf,valor_km ou cliente,valor_km.")
        parser.add_argument("--dry-run", action="store_true", help="Mostra o que faria sem gravar.")
        parser.add_argument("--confirmar", action="store_true", help="Confirma gravacao dos valores.")

    def handle(self, *args, **options):
        caminho = Path(options["arquivo_csv"])
        if not caminho.exists():
            raise CommandError(f"Arquivo nao encontrado: {caminho}")
        if not options["dry_run"] and not options["confirmar"]:
            raise CommandError("Use --dry-run para testar ou --confirmar para gravar.")

        atualizados = 0
        ambiguos = []
        nao_encontrados = []
        invalidos = []

        with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
            reader = csv.DictReader(arquivo)
            for linha_num, linha in enumerate(reader, start=2):
                valor_raw = linha.get("valor_km")
                try:
                    valor = normalizar_valor_km(valor_raw)
                except Exception as exc:
                    invalidos.append(f"linha {linha_num}: valor_km invalido ({exc})")
                    continue

                cnpj = _somente_digitos(linha.get("cnpj_cpf"))
                cliente_nome = (linha.get("cliente") or "").strip()
                cliente = None
                if cnpj:
                    cliente = Cliente.objects.filter(cnpj_cpf=cnpj).first()
                if cliente is None and cliente_nome:
                    nome_normalizado = normalizar_texto_busca(cliente_nome)
                    candidatos = [
                        c
                        for c in Cliente.objects.all()
                        if normalizar_texto_busca(c.nome_exibicao) == nome_normalizado
                        or normalizar_texto_busca(c.razao_social) == nome_normalizado
                        or normalizar_texto_busca(c.nome) == nome_normalizado
                    ]
                    if len(candidatos) == 1:
                        cliente = candidatos[0]
                    elif len(candidatos) > 1:
                        ambiguos.append(f"linha {linha_num}: {cliente_nome}")
                        continue

                if cliente is None:
                    nao_encontrados.append(f"linha {linha_num}: {cnpj or cliente_nome or 'sem identificador'}")
                    continue

                if not options["dry_run"]:
                    cliente.valor_km = valor
                    cliente.save(update_fields=["valor_km"])
                atualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Importacao concluida. atualizados={atualizados}, ambiguos={len(ambiguos)}, "
                f"nao_encontrados={len(nao_encontrados)}, invalidos={len(invalidos)}, dry_run={options['dry_run']}"
            )
        )
        for titulo, itens in [
            ("Ambiguos", ambiguos),
            ("Nao encontrados", nao_encontrados),
            ("Invalidos", invalidos),
        ]:
            if itens:
                self.stdout.write(self.style.WARNING(titulo + ":"))
                for item in itens[:30]:
                    self.stdout.write(f"- {item}")
