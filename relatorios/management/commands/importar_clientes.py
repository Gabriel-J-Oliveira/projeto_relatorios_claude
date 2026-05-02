import csv
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand
from relatorios.models import Cliente


class Command(BaseCommand):
    help = "Importa clientes com valor_km a partir de um CSV"

    def add_arguments(self, parser):
        parser.add_argument("caminho", type=str, help="Caminho do arquivo CSV")

    def handle(self, *args, **options):
        caminho = options["caminho"]

        criados = 0
        atualizados = 0
        erros = 0

        try:
            # 🔥 CORREÇÃO DO BOM AQUI
            with open(caminho, newline="", encoding="utf-8-sig") as csvfile:
                reader = csv.DictReader(csvfile, delimiter=";")

                # 🔍 DEBUG INICIAL
                self.stdout.write(self.style.WARNING("Testando leitura do CSV..."))
                primeira = next(reader, None)
                if not primeira:
                    self.stdout.write(self.style.ERROR("CSV vazio ou inválido."))
                    return

                self.stdout.write(f"Colunas encontradas: {list(primeira.keys())}")
                self.stdout.write(f"Primeira linha: {primeira}")

                # Volta pro início
                csvfile.seek(0)
                reader = csv.DictReader(csvfile, delimiter=";")

                for i, row in enumerate(reader, start=1):
                    nome = (row.get("cliente") or "").strip()
                    valor_raw = (row.get("valor por km") or "").strip()

                    if not nome:
                        erros += 1
                        self.stdout.write(self.style.ERROR(f"Linha {i}: nome vazio"))
                        continue

                    if not valor_raw:
                        valor = Decimal("0")
                    else:
                        try:
                            valor = Decimal(valor_raw.replace(",", "."))
                        except InvalidOperation:
                            erros += 1
                            self.stdout.write(
                                self.style.ERROR(
                                    f"Linha {i}: valor inválido -> {valor_raw}"
                                )
                            )
                            continue

                    cliente, created = Cliente.objects.get_or_create(
                        nome=nome,
                        defaults={
                            "valor_km": valor,
                        },
                    )

                    if created:
                        criados += 1
                    else:
                        # Atualiza valor_km se já existir
                        cliente.valor_km = valor
                        cliente.save()
                        atualizados += 1

        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"Arquivo não encontrado: {caminho}"))
            return

        # 📊 RESUMO FINAL
        self.stdout.write(self.style.SUCCESS("\n=== IMPORTAÇÃO FINALIZADA ==="))
        self.stdout.write(f"Clientes criados: {criados}")
        self.stdout.write(f"Clientes atualizados: {atualizados}")
        self.stdout.write(f"Erros: {erros}")