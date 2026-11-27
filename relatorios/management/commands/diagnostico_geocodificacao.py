import json
from time import perf_counter, sleep

from django.core.management.base import BaseCommand, CommandError

from relatorios.services.maps_service import MapsServiceError, buscar_endereco


class Command(BaseCommand):
    help = "Compara buscas explícitas Geoapify e Nominatim; consome créditos da API."

    def add_arguments(self, parser):
        parser.add_argument("--endereco", action="append", dest="enderecos")
        parser.add_argument("--json", action="store_true", dest="saida_json")

    def handle(self, *args, **options):
        enderecos = options["enderecos"] or [
            "Av. da Fag, 205 - Santo Inácio, Cascavel - PR, 85806-096",
            "BR277 KM 627", "85806-096", "Supermercado Muffato, Cascavel",
            "Supermercado Festval, Cascavel", "Prefeitura de Cascavel", "Aeroporto de Cascavel",
            "Rua Raimundo Leonardi, 1586 - Centro, Toledo - PR, 85900-110",
            "Praça Getúlio Vargas, 280 - Centro, Foz do Iguaçu - PR, 85851-340",
            "Rua Paraná, Cascavel - PR", "Rua Paraná, Toledo - PR",
            "Rua de teste inexistente ZQX, Cascavel - PR",
        ]
        falhou = False
        registros = []
        for endereco in enderecos:
            if not options["saida_json"]:
                self.stdout.write(f"\nConsulta: {endereco}")
            for provider in ("geoapify", "nominatim"):
                if provider == "nominatim":
                    sleep(1.1)
                inicio = perf_counter()
                metricas = {"chamadas": 0}
                resultados, erro = [], None
                try:
                    resultados = buscar_endereco(endereco, modo="search", provider=provider, metricas=metricas)
                except MapsServiceError as exc:
                    falhou = True
                    erro = str(exc)
                ms = round((perf_counter() - inicio) * 1000)
                registros.append({"consulta": endereco, "provedor": provider, "ms": ms, **metricas, "erro": erro, "resultados": resultados})
                if not options["saida_json"]:
                    self.stdout.write(f"{provider}: {len(resultados)} resultado(s) em {ms} ms, {metricas['chamadas']} chamada(s)")
                    if erro:
                        self.stderr.write(f"  {erro}")
                    for item in resultados:
                        self.stdout.write(f"  {item['display_name']} | {item.get('municipio', '')}/{item.get('uf', '')} | {item.get('result_type', '')} | confiança: {(item.get('rank') or {}).get('confidence', 'n/d')}")
        if options["saida_json"]:
            self.stdout.write(json.dumps(registros, ensure_ascii=False, indent=2))
        if falhou:
            raise CommandError("Uma ou mais consultas falharam.")
