from relatorios.services.identidade.grupo_mapping_service import extrair_cn
from relatorios.services.identidade.sincronizacao_service import UsuarioExternoSnapshot


UF_ACCOUNTDISABLE = 0x0002
PRIMARY_GROUP_DOMAIN_USERS = 513
WINDOWS_FILETIME_EPOCH_OFFSET = 116444736000000000
WINDOWS_FILETIME_TICKS_PER_SECOND = 10000000
ACCOUNT_EXPIRES_NEVER = {
    "",
    "0",
    "9223372036854775807",
}


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


def inteiro_atributo(attrs, nome, default=0):
    valor = primeiro_atributo(attrs, nome)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


def conta_ad_desativada(attrs):
    return bool(inteiro_atributo(attrs, "userAccountControl") & UF_ACCOUNTDISABLE)


def conta_ad_bloqueada(attrs):
    return inteiro_atributo(attrs, "lockoutTime") > 0


def conta_ad_expirada(attrs, agora_timestamp=None):
    valor = primeiro_atributo(attrs, "accountExpires")
    if valor in ACCOUNT_EXPIRES_NEVER:
        return False
    try:
        filetime = int(valor)
    except (TypeError, ValueError):
        return False
    if filetime <= 0:
        return False
    if agora_timestamp is None:
        import time

        agora_timestamp = time.time()
    expira_em = (filetime - WINDOWS_FILETIME_EPOCH_OFFSET) / WINDOWS_FILETIME_TICKS_PER_SECOND
    return expira_em <= agora_timestamp


def usuario_ad_ativo(attrs):
    return not (
        conta_ad_desativada(attrs)
        or conta_ad_bloqueada(attrs)
        or conta_ad_expirada(attrs)
    )


def status_conta_ad(attrs):
    status = []
    if conta_ad_desativada(attrs):
        status.append("desativada")
    if conta_ad_bloqueada(attrs):
        status.append("bloqueada")
    if conta_ad_expirada(attrs):
        status.append("expirada")
    return tuple(status)


def extrair_grupos_ad(ldap_user=None, attrs=None):
    grupos = set()
    group_dns = getattr(ldap_user, "group_dns", None)
    if group_dns:
        grupos.update(_normalizar_valor_ldap(grupo) for grupo in group_dns)

    attrs = attrs or getattr(ldap_user, "attrs", {})
    grupos.update(listar_atributo(attrs, "memberOf"))
    if inteiro_atributo(attrs, "primaryGroupID") == PRIMARY_GROUP_DOMAIN_USERS:
        grupos.add("Domain Users")
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
        is_active=usuario_ad_ativo(attrs),
        grupos_ad=tuple(grupos_ad or ()),
        identificador_externo=primeiro_atributo(attrs, "distinguishedName"),
    )


def descrever_grupos_ad(grupos_ad):
    return tuple(
        f"{extrair_cn(grupo)} ({grupo})" if extrair_cn(grupo) != grupo else grupo
        for grupo in grupos_ad or ()
    )
