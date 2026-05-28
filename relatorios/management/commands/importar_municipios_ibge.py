import logging

import requests
from django.core.management.base import BaseCommand, CommandError

from relatorios.models import Municipio, TipoLocalidade, normalizar_texto_busca


logger = logging.getLogger(__name__)

IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"

CAPITAIS = {
    "AC": "Rio Branco",
    "AL": "Maceió",
    "AP": "Macapá",
    "AM": "Manaus",
    "BA": "Salvador",
    "CE": "Fortaleza",
    "DF": "Brasília",
    "ES": "Vitória",
    "GO": "Goiânia",
    "MA": "São Luís",
    "MT": "Cuiabá",
    "MS": "Campo Grande",
    "MG": "Belo Horizonte",
    "PA": "Belém",
    "PB": "João Pessoa",
    "PR": "Curitiba",
    "PE": "Recife",
    "PI": "Teresina",
    "RJ": "Rio de Janeiro",
    "RN": "Natal",
    "RS": "Porto Alegre",
    "RO": "Porto Velho",
    "RR": "Boa Vista",
    "SC": "Florianópolis",
    "SP": "São Paulo",
    "SE": "Aracaju",
    "TO": "Palmas",
}


class Command(BaseCommand):
    help = "Importa/atualiza municípios brasileiros pela API pública do IBGE."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Simula a importação sem gravar.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Sobrescreve também classificações manuais, inclusive FRONTEIRA.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        try:
            response = requests.get(IBGE_MUNICIPIOS_URL, timeout=30)
            response.raise_for_status()
            municipios = response.json()
        except requests.RequestException as exc:
            logger.exception("Falha ao baixar municípios do IBGE.")
            raise CommandError(f"Falha ao consultar IBGE: {exc}") from exc

        criados = atualizados = preservados = 0
        for item in municipios:
            microrregiao = item.get("microrregiao") or {}
            mesorregiao = microrregiao.get("mesorregiao") or {}
            uf_payload = mesorregiao.get("UF") or {}
            uf = uf_payload.get("sigla") or ""
            nome = item.get("nome") or ""
            if not uf or not nome:
                continue
            codigo_ibge = str(item.get("id") or "")
            uf_nome = uf_payload.get("nome") or ""
            eh_capital = normalizar_texto_busca(CAPITAIS.get(uf, "")) == normalizar_texto_busca(nome)
            tipo_padrao = TipoLocalidade.CAPITAL if eh_capital else TipoLocalidade.INTERIOR

            existente = Municipio.objects.filter(codigo_ibge=codigo_ibge).first()
            if existente and existente.tipo_localidade_padrao == TipoLocalidade.FRONTEIRA and not force:
                tipo_padrao = existente.tipo_localidade_padrao
                preservados += 1

            defaults = {
                "nome": nome,
                "nome_normalizado": normalizar_texto_busca(nome),
                "uf": uf,
                "uf_nome": uf_nome,
                "eh_capital": eh_capital,
                "tipo_localidade_padrao": tipo_padrao,
                "ativo": True,
            }
            if dry_run:
                if existente:
                    atualizados += 1
                else:
                    criados += 1
                continue

            _obj, criado = Municipio.objects.update_or_create(
                codigo_ibge=codigo_ibge,
                defaults=defaults,
            )
            if criado:
                criados += 1
            else:
                atualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Municípios IBGE processados: {len(municipios)} | criados={criados} | "
                f"atualizados={atualizados} | fronteiras_preservadas={preservados} | dry_run={dry_run}"
            )
        )
