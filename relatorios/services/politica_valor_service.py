import logging
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from relatorios.models import PoliticaValor, TipoDespesa, TipoLocalidade


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoliticaAplicavel:
    chave: str
    descricao: str
    valor: Decimal
    tipo_politica: str
    excede: bool
    excesso: Decimal


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-zA-Z0-9]+", " ", texto).strip().upper()
    return " ".join(texto.split())


def _money(valor):
    return Decimal(str(valor or "0.00")).quantize(Decimal("0.01"))


def _buscar_chave(chave, data):
    politica = PoliticaValor.vigente_por_chave(chave, data or timezone.localdate())
    if not politica:
        logger.info("politica_nao_encontrada chave=%s data=%s", chave, data)
    return politica


def _politica_payload(politica, valor_informado=None):
    if not politica:
        return None
    valor = _money(politica.limite_valor or politica.valor_km)
    informado = _money(valor_informado)
    excesso = _money(max(informado - valor, Decimal("0.00")))
    return PoliticaAplicavel(
        chave=politica.chave,
        descricao=politica.descricao,
        valor=valor,
        tipo_politica=politica.tipo_politica,
        excede=excesso > 0,
        excesso=excesso,
    )


def _cidade_chave(cidade):
    normalizada = _normalizar(cidade)
    if "MARINGA" in normalizada or "LONDRINA" in normalizada:
        return "MARINGA_LONDRINA"
    mapa = {
        "CASCAVEL": "CASCAVEL",
        "PATO BRANCO": "PATO_BRANCO",
        "CURITIBA": "CURITIBA",
        "PONTA GROSSA": "PONTA_GROSSA",
        "IRATI": "IRATI",
        "SAO PAULO": "SAO_PAULO",
        "ITAJAI": "ITAJAI",
        "CAMPO GRANDE": "CAMPO_GRANDE",
        "DOURADOS": "DOURADOS",
    }
    for nome, chave in mapa.items():
        if nome in normalizada:
            return chave
    return ""


def _rota_chave(texto):
    normalizado = _normalizar(texto)
    cidades = {
        "CASCAVEL": "CASCAVEL",
        "CAMPO GRANDE": "CAMPO_GRANDE",
        "SAO PAULO": "SAO_PAULO",
        "CURITIBA": "CURITIBA",
        "MARINGA": "MARINGA",
    }
    presentes = [chave for nome, chave in cidades.items() if nome in normalizado]
    rotas = {
        frozenset(("CASCAVEL", "CAMPO_GRANDE")): "PASSAGEM_CASCAVEL_CAMPO_GRANDE",
        frozenset(("CASCAVEL", "SAO_PAULO")): "PASSAGEM_CASCAVEL_SAO_PAULO",
        frozenset(("CURITIBA", "CAMPO_GRANDE")): "PASSAGEM_CURITIBA_CAMPO_GRANDE",
        frozenset(("CURITIBA", "SAO_PAULO")): "PASSAGEM_CURITIBA_SAO_PAULO",
        frozenset(("CURITIBA", "MARINGA")): "PASSAGEM_CURITIBA_MARINGA",
        frozenset(("CURITIBA", "CASCAVEL")): "PASSAGEM_CURITIBA_CASCAVEL",
    }
    for par, chave in rotas.items():
        if par.issubset(set(presentes)):
            return chave
    return ""


def resolver_politica_despesa(
    *,
    tipo_despesa,
    data,
    tipo_localidade="",
    cidade="",
    municipio=None,
    descricao="",
    valor_informado=None,
):
    if municipio is not None:
        cidade = getattr(municipio, "nome", "") or cidade
        tipo_localidade = tipo_localidade or getattr(municipio, "tipo_localidade_padrao", "")

    texto = f"{cidade} {descricao}"
    chave = ""

    if tipo_despesa == TipoDespesa.ALIMENTACAO:
        chave = (
            "REFEICAO_CAPITAL"
            if tipo_localidade == TipoLocalidade.CAPITAL
            else "REFEICAO_INTERIOR"
        )
    elif tipo_despesa == TipoDespesa.HOSPEDAGEM:
        cidade_chave = _cidade_chave(texto)
        if cidade_chave:
            chave = f"HOSPEDAGEM_{cidade_chave}"
    elif tipo_despesa == TipoDespesa.TRANSPORTE:
        rota = _rota_chave(texto)
        if rota:
            chave = rota
        else:
            cidade_chave = _cidade_chave(texto)
            if cidade_chave:
                chave = f"KM_DIARIO_{cidade_chave}"

    if not chave:
        return None
    return _politica_payload(_buscar_chave(chave, data), valor_informado)


def valor_km_control_sul(data=None):
    politica = _buscar_chave("VALOR_KM_CONTROLSUL", data or timezone.localdate())
    if politica and politica.valor_km:
        return _money(politica.valor_km)
    return Decimal("1.35")
