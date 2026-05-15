from django.conf import settings
from django.contrib.auth.models import Group

from relatorios.services.autorizacao_service import GRUPOS_ERP


def normalizar_identificador_grupo(valor):
    return (valor or "").strip().casefold()


def extrair_cn(valor):
    """
    Extrai CN simples de um DN LDAP, mantendo compatibilidade com nomes comuns.
    Ex.: CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local -> ERP-Financeiro
    """
    partes = [parte.strip() for parte in (valor or "").split(",") if parte.strip()]
    for parte in partes:
        chave, separador, conteudo = parte.partition("=")
        if separador and chave.strip().casefold() == "cn":
            return conteudo.strip()
    return valor


def aliases_grupo_ad(valor):
    cn = extrair_cn(valor)
    return {
        normalizar_identificador_grupo(valor),
        normalizar_identificador_grupo(cn),
    }


def obter_mapeamento_grupos_ad():
    """
    Fonte atual do mapeamento AD -> Django.

    Mantida em settings para ser estável em deploy e fácil de substituir por
    banco/admin no futuro sem acoplar as views ao LDAP.
    """
    return dict(getattr(settings, "AD_GROUP_MAPPING", {}) or {})


def validar_mapeamento_grupos_ad(mapeamento=None):
    mapeamento = obter_mapeamento_grupos_ad() if mapeamento is None else dict(mapeamento)
    grupos_invalidos = sorted(
        {
            grupo_django
            for grupo_django in mapeamento.values()
            if grupo_django not in GRUPOS_ERP
        }
    )
    return {
        "valido": not grupos_invalidos,
        "grupos_invalidos": grupos_invalidos,
    }


def mapear_grupos_ad_para_django(grupos_ad, mapeamento=None):
    """
    Resolve uma lista de grupos vindos do AD para nomes de grupos Django.

    Aceita tanto DN completo quanto CN/nome simples. Não cria grupos e não toca
    no usuário; apenas traduz identidades externas para papéis internos.
    """
    mapeamento = obter_mapeamento_grupos_ad() if mapeamento is None else dict(mapeamento)
    grupos_resolvidos = set()
    aliases_recebidos = set()

    for grupo_ad in grupos_ad or []:
        aliases_recebidos.update(aliases_grupo_ad(grupo_ad))

    for grupo_ad_mapeado, grupo_django in mapeamento.items():
        if grupo_django not in GRUPOS_ERP:
            continue
        if aliases_grupo_ad(grupo_ad_mapeado) & aliases_recebidos:
            grupos_resolvidos.add(grupo_django)

    return sorted(grupos_resolvidos)


def garantir_grupos_erp():
    grupos = []
    for nome in GRUPOS_ERP:
        grupo, _criado = Group.objects.get_or_create(name=nome)
        grupos.append(grupo)
    return grupos

