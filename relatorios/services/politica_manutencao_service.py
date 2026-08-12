from types import SimpleNamespace

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from relatorios.models import (
    EmpresaGrupo,
    EscopoPoliticaValor,
    PoliticaValor,
    PoliticaValorEmpresaGrupo,
    TipoLocalidade,
)


STATUS_POLITICA_CHOICES = [
    ("ativa", "Ativa"),
    ("futura", "Futura"),
    ("encerrada", "Encerrada"),
    ("desativada", "Desativada"),
]


def status_politica(politica, hoje=None):
    hoje = hoje or timezone.localdate()
    if not politica.ativo:
        return SimpleNamespace(codigo="desativada", label="Desativada", badge="text-bg-secondary")
    if politica.vigencia_inicio and politica.vigencia_inicio > hoje:
        return SimpleNamespace(codigo="futura", label="Futura", badge="text-bg-info")
    if politica.vigencia_fim and politica.vigencia_fim < hoje:
        return SimpleNamespace(codigo="encerrada", label="Encerrada", badge="text-bg-dark")
    return SimpleNamespace(codigo="ativa", label="Ativa", badge="text-bg-success")


def valor_politica_label(politica):
    if politica.valor_km is not None:
        return f"R$ {politica.valor_km:.4f}/km".replace(".", ",")
    if politica.limite_valor is not None:
        return f"R$ {politica.limite_valor:.2f}".replace(".", ",")
    return "-"


def localidade_politica_label(politica):
    partes = []
    if politica.tipo_localidade:
        partes.append(politica.get_tipo_localidade_display())
    if politica.cidade:
        partes.append(politica.cidade)
    rota = " -> ".join([valor for valor in [politica.origem, politica.destino] if valor])
    if rota:
        partes.append(rota)
    return " | ".join(partes) if partes else "-"


def empresas_politica(politica):
    empresas = list(politica.empresas_grupo.all())
    labels = dict(EmpresaGrupo.choices)
    return [labels.get(item.empresa_grupo, item.empresa_grupo) for item in empresas]


def escopo_politica_label(politica):
    if politica.escopo == EscopoPoliticaValor.GLOBAL:
        return "GLOBAL"
    return "EMPRESAS"


def filtrar_politicas(params):
    hoje = timezone.localdate()
    qs = PoliticaValor.objects.prefetch_related("empresas_grupo").order_by(
        "chave",
        "-vigencia_inicio",
        "-pk",
    )

    termo = (params.get("q") or "").strip()
    if termo:
        qs = qs.filter(
            Q(chave__icontains=termo)
            | Q(descricao__icontains=termo)
            | Q(cidade__icontains=termo)
            | Q(origem__icontains=termo)
            | Q(destino__icontains=termo)
        )

    tipo_politica = params.get("tipo_politica")
    if tipo_politica:
        qs = qs.filter(tipo_politica=tipo_politica)

    tipo_despesa = params.get("tipo_despesa")
    if tipo_despesa:
        qs = qs.filter(tipo_despesa=tipo_despesa)

    escopo = params.get("escopo")
    if escopo:
        qs = qs.filter(escopo=escopo)

    empresa = params.get("empresa")
    if empresa:
        qs = qs.filter(empresas_grupo__empresa_grupo=empresa).distinct()

    localidade = params.get("localidade")
    if localidade:
        qs = qs.filter(tipo_localidade=localidade)

    status = params.get("status")
    if status == "desativada":
        qs = qs.filter(ativo=False)
    elif status == "futura":
        qs = qs.filter(ativo=True, vigencia_inicio__gt=hoje)
    elif status == "encerrada":
        qs = qs.filter(ativo=True, vigencia_fim__lt=hoje)
    elif status == "ativa":
        qs = qs.filter(ativo=True, vigencia_inicio__lte=hoje).filter(
            Q(vigencia_fim__isnull=True) | Q(vigencia_fim__gte=hoje)
        )
    return qs


def preparar_linhas_politicas(politicas):
    linhas = []
    for politica in politicas:
        status = status_politica(politica)
        linhas.append(
            SimpleNamespace(
                politica=politica,
                status=status,
                escopo_label=escopo_politica_label(politica),
                empresas=empresas_politica(politica),
                valor_label=valor_politica_label(politica),
                localidade_label=localidade_politica_label(politica),
                configuracao_invalida=(
                    politica.escopo == EscopoPoliticaValor.EMPRESAS
                    and not politica.empresas_grupo.exists()
                ),
            )
        )
    return linhas


@transaction.atomic
def salvar_politica_manutencao(form):
    politica = form.instance or PoliticaValor()
    cleaned = form.cleaned_data
    for campo in [
        "chave",
        "descricao",
        "tipo_politica",
        "tipo_despesa",
        "tipo_localidade",
        "cidade",
        "origem",
        "destino",
        "limite_valor",
        "valor_km",
        "vigencia_inicio",
        "vigencia_fim",
        "ativo",
        "escopo",
    ]:
        setattr(politica, campo, cleaned.get(campo))
    politica.save()

    politica.empresas_grupo.all().delete()
    if politica.escopo == EscopoPoliticaValor.EMPRESAS:
        PoliticaValorEmpresaGrupo.objects.bulk_create(
            [
                PoliticaValorEmpresaGrupo(politica=politica, empresa_grupo=empresa)
                for empresa in cleaned.get("empresas", [])
            ]
        )
    return politica


def dados_iniciais_duplicacao(politica):
    return {
        "chave": politica.chave,
        "descricao": politica.descricao,
        "tipo_politica": politica.tipo_politica,
        "tipo_despesa": politica.tipo_despesa,
        "tipo_localidade": politica.tipo_localidade,
        "cidade": politica.cidade,
        "origem": politica.origem,
        "destino": politica.destino,
        "limite_valor": politica.limite_valor,
        "valor_km": politica.valor_km,
        "vigencia_inicio": politica.vigencia_inicio,
        "vigencia_fim": politica.vigencia_fim,
        "ativo": politica.ativo,
        "escopo": politica.escopo,
        "empresas": list(politica.empresas_grupo.values_list("empresa_grupo", flat=True)),
    }


@transaction.atomic
def encerrar_politica(politica, data_fim=None):
    politica.vigencia_fim = data_fim or timezone.localdate()
    politica.save(update_fields=["vigencia_fim"])
    return politica


def opcoes_filtro_politicas():
    return {
        "tipo_politica_choices": PoliticaValor.TipoPolitica.choices,
        "tipo_despesa_choices": PoliticaValor._meta.get_field("tipo_despesa").choices,
        "escopo_choices": EscopoPoliticaValor.choices,
        "empresa_choices": EmpresaGrupo.choices,
        "localidade_choices": TipoLocalidade.choices,
        "status_choices": STATUS_POLITICA_CHOICES,
    }
