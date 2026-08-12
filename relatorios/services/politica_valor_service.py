import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from relatorios.models import (
    EmpresaGrupo,
    EscopoPoliticaValor,
    PoliticaValor,
    TipoDespesa,
    TipoLocalidade,
)


logger = logging.getLogger(__name__)

TIPOS_DESPESA_SEM_POLITICA = {TipoDespesa.PASSAGEM, TipoDespesa.TRANSPORTE}
TIPOS_DESPESA_POLITICA_POR_TECNICO = {
    TipoDespesa.ALIMENTACAO,
    TipoDespesa.HOSPEDAGEM,
}


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
    if valor is None or valor == "":
        return None
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def _money_zero(valor):
    numero = _money(valor)
    return numero if numero is not None else Decimal("0.00")


def _positive_int(valor, default=1):
    try:
        numero = int(valor or default)
    except (TypeError, ValueError):
        numero = default
    return max(numero, default)


def despesa_usa_politica_por_tecnico(tipo_despesa):
    return str(tipo_despesa or "") in {
        str(tipo) for tipo in TIPOS_DESPESA_POLITICA_POR_TECNICO
    }


def _fim_ou_infinito(valor):
    return valor or date.max


def politicas_empresariais_conflitantes(politica, empresas=None):
    empresas = [empresa for empresa in (empresas or []) if empresa]
    if not politica or politica.escopo != EscopoPoliticaValor.EMPRESAS or not empresas:
        return PoliticaValor.objects.none()

    inicio = politica.vigencia_inicio
    fim = _fim_ou_infinito(politica.vigencia_fim)
    qs = (
        PoliticaValor.objects.filter(
            chave=politica.chave,
            escopo=EscopoPoliticaValor.EMPRESAS,
            ativo=True,
            empresas_grupo__empresa_grupo__in=empresas,
            vigencia_inicio__lte=fim,
        )
        .filter(Q(vigencia_fim__isnull=True) | Q(vigencia_fim__gte=inicio))
        .distinct()
    )
    if politica.pk:
        qs = qs.exclude(pk=politica.pk)
    return qs


def validar_configuracao_politica_empresas(politica, empresas=None):
    empresas_validas = {valor for valor, _label in EmpresaGrupo.choices}
    empresas = [empresa for empresa in (empresas or []) if empresa]
    empresas_invalidas = sorted(set(empresas) - empresas_validas)
    if empresas_invalidas:
        raise ValidationError(
            f"Empresa(s) inválida(s) para política: {', '.join(empresas_invalidas)}."
        )

    if politica.escopo == EscopoPoliticaValor.GLOBAL:
        if empresas:
            raise ValidationError("Políticas globais não devem possuir empresas específicas.")
        return

    if politica.escopo != EscopoPoliticaValor.EMPRESAS:
        raise ValidationError("Escopo de política inválido.")
    if not empresas:
        raise ValidationError("Informe ao menos uma empresa para política empresarial.")

    conflitos = politicas_empresariais_conflitantes(politica, empresas)
    if conflitos.exists():
        conflitantes = ", ".join(
            f"#{item.pk} {item.chave}" for item in conflitos[:5]
        )
        raise ValidationError(
            "Já existe política empresarial ativa com a mesma chave, empresa e vigência "
            f"sobreposta: {conflitantes}."
        )


def calcular_limite_politica_despesa(
    politica,
    *,
    tipo_despesa,
    quantidade_tecnicos=1,
    diarias=0,
):
    if not politica:
        return None

    valor_base = _money(getattr(politica, "valor", None))
    if valor_base is None:
        return None

    multiplicador = Decimal("1")
    if despesa_usa_politica_por_tecnico(tipo_despesa):
        multiplicador *= Decimal(_positive_int(quantidade_tecnicos))
    if tipo_despesa == TipoDespesa.HOSPEDAGEM and _positive_int(diarias, default=0) > 0:
        multiplicador *= Decimal(_positive_int(diarias, default=0))

    return _money(valor_base * multiplicador)


def _buscar_chave(chave, data, empresa_grupo=None):
    politica = PoliticaValor.vigente_por_chave(
        chave,
        data or timezone.localdate(),
        empresa_grupo=empresa_grupo,
    )
    if not politica:
        logger.info(
            "politica_nao_encontrada chave=%s data=%s empresa_grupo=%s",
            chave,
            data,
            empresa_grupo or "",
        )
    return politica


def _politica_payload(politica, valor_informado=None):
    if not politica:
        return None
    valor_origem = politica.limite_valor if politica.limite_valor is not None else politica.valor_km
    valor = _money(valor_origem)
    if valor is None:
        logger.warning(
            "politica_sem_valor chave=%s tipo=%s data_inicio=%s",
            getattr(politica, "chave", ""),
            getattr(politica, "tipo_politica", ""),
            getattr(politica, "vigencia_inicio", None),
        )
        return None
    informado = _money_zero(valor_informado)
    excesso = _money_zero(max(informado - valor, Decimal("0.00")))
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
    empresa_grupo=None,
):
    if tipo_despesa in TIPOS_DESPESA_SEM_POLITICA:
        return None

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

    if not chave:
        return None
    return _politica_payload(
        _buscar_chave(chave, data, empresa_grupo=empresa_grupo),
        valor_informado,
    )


def valor_km_control_sul(data=None):
    politica = _buscar_chave("VALOR_KM_CONTROLSUL", data or timezone.localdate())
    if politica and politica.valor_km:
        return _money(politica.valor_km) or Decimal("1.35")
    return Decimal("1.35")
