from relatorios.services.identidade.grupo_mapping_service import extrair_cn
from relatorios.services.identidade.sincronizacao_service import UsuarioExternoSnapshot


def normalizar_username_ad(username):
    valor = (username or "").strip()
    if "\\" in valor:
        valor = valor.rsplit("\\", 1)[-1]
    if "@" in valor:
        valor = valor.split("@", 1)[0]
    return valor.strip().lower()


def _normalizar_valor_ldap(valor):
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="ignore").strip()
    return str(valor or "").strip()


def primeiro_atributo(attrs, nome):
    valores = (attrs or {}).get(nome) or []
    if not valores:
        return ""
    return _normalizar_valor_ldap(valores[0])


def listar_atributo(attrs, nome):
    return tuple(
        valor
        for valor in (_normalizar_valor_ldap(item) for item in (attrs or {}).get(nome, []))
        if valor
    )


def extrair_grupos_ad(ldap_user=None, attrs=None):
    grupos = set()
    group_dns = getattr(ldap_user, "group_dns", None)
    if group_dns:
        grupos.update(_normalizar_valor_ldap(grupo) for grupo in group_dns)

    grupos.update(listar_atributo(attrs or getattr(ldap_user, "attrs", {}), "memberOf"))
    return tuple(sorted(grupo for grupo in grupos if grupo))


def construir_snapshot_ldap(username, attrs, grupos_ad=None):
    username_normalizado = normalizar_username_ad(
        primeiro_atributo(attrs, "sAMAccountName") or username
    )
    email = primeiro_atributo(attrs, "mail") or primeiro_atributo(attrs, "userPrincipalName")
    first_name = primeiro_atributo(attrs, "givenName")
    last_name = primeiro_atributo(attrs, "sn")

    if not first_name and primeiro_atributo(attrs, "displayName"):
        partes_nome = primeiro_atributo(attrs, "displayName").split()
        first_name = partes_nome[0]
        last_name = " ".join(partes_nome[1:])

    return UsuarioExternoSnapshot(
        username=username_normalizado,
        email=email,
        first_name=first_name,
        last_name=last_name,
        grupos_ad=tuple(grupos_ad or ()),
        identificador_externo=primeiro_atributo(attrs, "distinguishedName"),
    )


def descrever_grupos_ad(grupos_ad):
    return tuple(
        f"{extrair_cn(grupo)} ({grupo})" if extrair_cn(grupo) != grupo else grupo
        for grupo in grupos_ad or ()
    )

