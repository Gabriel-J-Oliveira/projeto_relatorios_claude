from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from relatorios.models import (
    EstadoHistoricoPermissao,
    EstadoPermissaoUsuario,
    HistoricoPermissaoUsuario,
    PermissaoUsuarioOverride,
)
from relatorios.services.permissoes_service import (
    CategoriaPermissao,
    CodigoPermissao,
    SensibilidadePermissao,
    definir_override_permissao,
    listar_permissoes,
    obter_permissao,
    usuario_pode_acessar_central_permissoes,
    usuario_tem_full_access_erp,
)


CODIGOS_CENTRAL_USUARIOS_V1 = (
    CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
    CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
    CodigoPermissao.FINANCEIRO_ACESSAR,
    CodigoPermissao.RELATORIOS_APROVAR,
    CodigoPermissao.RELATORIOS_REJEITAR,
    CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE,
    CodigoPermissao.FINANCEIRO_ALTERAR_VALORES,
    CodigoPermissao.FINANCEIRO_ALTERAR_ITENS,
    CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS,
    CodigoPermissao.RELATORIOS_PDF_INTERNO,
    CodigoPermissao.RELATORIOS_REABRIR,
    CodigoPermissao.USUARIOS_GERENCIAR,
    CodigoPermissao.MANUTENCAO_ACESSAR,
    CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
    CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM,
    CodigoPermissao.AJUDA_EDITAR,
    CodigoPermissao.PERMISSOES_GERENCIAR,
)

CATEGORIAS_UI = (
    (CategoriaPermissao.RELATORIOS, "Relatorios"),
    (CategoriaPermissao.FINANCEIRO, "Financeiro"),
    (CategoriaPermissao.ADMINISTRACAO, "Administracao"),
    (CategoriaPermissao.CADASTROS, "Cadastros"),
    (CategoriaPermissao.MANUTENCAO, "Manutencao"),
    (CategoriaPermissao.AJUDA, "Ajuda"),
)

ESTADOS_VALIDOS = {
    EstadoHistoricoPermissao.HERDAR,
    EstadoPermissaoUsuario.PERMITIR,
    EstadoPermissaoUsuario.NEGAR,
}

DEPENDENCIAS_PERMISSOES = {
    CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
    ),
    CodigoPermissao.RELATORIOS_APROVAR: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.RELATORIOS_REJEITAR: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.FINANCEIRO_ALTERAR_VALORES: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.FINANCEIRO_ALTERAR_ITENS: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
    CodigoPermissao.RELATORIOS_PDF_INTERNO: (
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
        CodigoPermissao.FINANCEIRO_ACESSAR,
    ),
}


class PermissaoCentralError(ValueError):
    pass


@dataclass(frozen=True)
class EstadoPermissaoApresentacao:
    permissao: object
    override: str
    efetivo_permitido: bool
    origem: str
    readonly: bool
    dependencias: tuple[str, ...]
    dependencias_pendentes: tuple[str, ...]


def queryset_usuarios_central(params):
    User = get_user_model()
    qs = (
        User.objects.all()
        .prefetch_related("groups", "permissoes_overrides")
        .annotate(overrides_count=Count("permissoes_overrides", distinct=True))
        .order_by("first_name", "username")
    )
    termo = (params.get("q") or "").strip()
    if termo:
        qs = qs.filter(
            Q(username__icontains=termo)
            | Q(first_name__icontains=termo)
            | Q(last_name__icontains=termo)
            | Q(email__icontains=termo)
        )
    ativo = params.get("ativo")
    if ativo == "1":
        qs = qs.filter(is_active=True)
    elif ativo == "0":
        qs = qs.filter(is_active=False)
    grupo = (params.get("grupo") or "").strip()
    if grupo:
        qs = qs.filter(groups__name=grupo).distinct()
    if params.get("overrides") == "1":
        qs = qs.filter(permissoes_overrides__isnull=False).distinct()
    return qs


def opcoes_grupos_usuarios():
    User = get_user_model()
    return (
        User.objects.filter(groups__isnull=False)
        .values_list("groups__name", flat=True)
        .distinct()
        .order_by("groups__name")
    )


def grupos_usuario(usuario):
    return [grupo.name for grupo in usuario.groups.all()]


def usuario_origem_label(usuario):
    if grupos_usuario(usuario):
        return "Active Directory / Grupos Django"
    return "Usuario Django"


def motor_atual_label():
    return "Central" if getattr(settings, "PERMISSOES_CENTRAL_ENABLED", False) else "Legado"


def _overrides_map(usuario):
    return {override.codigo: override.estado for override in usuario.permissoes_overrides.all()}


def _estado_efetivo_basico(usuario, codigo, overrides):
    if usuario_tem_full_access_erp(usuario):
        return True, "Full Access protegido"
    estado = overrides.get(codigo)
    if estado == EstadoPermissaoUsuario.PERMITIR:
        return True, "Override individual"
    if estado == EstadoPermissaoUsuario.NEGAR:
        return False, "Negacao individual"
    if codigo in {CodigoPermissao.ERP_ACESSAR, CodigoPermissao.RELATORIOS_CRIAR}:
        return True, "Padrao do sistema"
    return False, "Padrao do sistema"


def permissoes_usuario_para_ui(usuario):
    overrides = _overrides_map(usuario)
    linhas = []
    for codigo in CODIGOS_CENTRAL_USUARIOS_V1:
        permissao = obter_permissao(codigo)
        if not permissao:
            continue
        dependencias = DEPENDENCIAS_PERMISSOES.get(codigo, ())
        dependencias_pendentes = []
        for dependencia in dependencias:
            permitido, _origem = _estado_efetivo_basico(usuario, dependencia, overrides)
            if not permitido:
                dependencias_pendentes.append(dependencia)
        permitido, origem = _estado_efetivo_basico(usuario, codigo, overrides)
        linhas.append(
            EstadoPermissaoApresentacao(
                permissao=permissao,
                override=overrides.get(codigo, EstadoHistoricoPermissao.HERDAR),
                efetivo_permitido=permitido,
                origem=origem,
                readonly=usuario_tem_full_access_erp(usuario),
                dependencias=dependencias,
                dependencias_pendentes=tuple(dependencias_pendentes),
            )
        )
    return linhas


def permissoes_agrupadas_usuario(usuario):
    linhas = permissoes_usuario_para_ui(usuario)
    grupos = []
    for categoria, label in CATEGORIAS_UI:
        itens = [linha for linha in linhas if linha.permissao.categoria == categoria]
        if itens:
            grupos.append({"codigo": categoria, "label": label, "itens": itens})
    return grupos


def resumo_usuarios_linha(usuario):
    grupos = grupos_usuario(usuario)
    return {
        "usuario": usuario,
        "nome": usuario.get_full_name() or usuario.username,
        "grupos": grupos,
        "full_access": usuario_tem_full_access_erp(usuario),
        "overrides_count": getattr(usuario, "overrides_count", 0),
        "origem": usuario_origem_label(usuario),
    }


def historico_permissoes_usuario(usuario, limite=80):
    historicos = (
        HistoricoPermissaoUsuario.objects.filter(usuario_afetado=usuario)
        .select_related("alterado_por")
        .order_by("-criado_em", "-id")[:limite]
    )
    permissoes = {permissao.codigo: permissao for permissao in listar_permissoes()}
    linhas = []
    for item in historicos:
        permissao = permissoes.get(item.codigo)
        linhas.append(
            {
                "historico": item,
                "nome": permissao.nome if permissao else item.codigo,
                "codigo": item.codigo,
            }
        )
    return linhas


def validar_alteracao_override(*, administrador, usuario_alvo, codigo, estado):
    if not usuario_pode_acessar_central_permissoes(administrador):
        raise PermissaoCentralError("Usuario sem permissao para gerenciar permissoes.")
    permissao = obter_permissao(codigo)
    if not permissao or codigo not in CODIGOS_CENTRAL_USUARIOS_V1:
        raise PermissaoCentralError("Codigo de permissao invalido para esta Central.")
    if estado not in ESTADOS_VALIDOS:
        raise PermissaoCentralError("Estado de permissao invalido.")
    if usuario_tem_full_access_erp(usuario_alvo):
        raise PermissaoCentralError("Full Access protegido nao pode receber override pela interface.")
    if usuario_alvo.pk == administrador.pk and codigo == CodigoPermissao.PERMISSOES_GERENCIAR:
        raise PermissaoCentralError("Nao remova ou negue sua propria permissao de gerenciar permissoes.")
    if estado == EstadoPermissaoUsuario.PERMITIR:
        overrides = _overrides_map(usuario_alvo)
        overrides[codigo] = estado
        pendentes = []
        for dependencia in DEPENDENCIAS_PERMISSOES.get(codigo, ()):
            permitido, _origem = _estado_efetivo_basico(usuario_alvo, dependencia, overrides)
            if not permitido:
                pendentes.append(obter_permissao(dependencia).nome)
        if pendentes:
            raise PermissaoCentralError(
                "Antes de permitir esta capacidade, habilite: " + ", ".join(pendentes) + "."
            )
    return permissao


def salvar_override_central(*, administrador, usuario_alvo, codigo, estado):
    validar_alteracao_override(
        administrador=administrador,
        usuario_alvo=usuario_alvo,
        codigo=codigo,
        estado=estado,
    )
    definir_override_permissao(usuario_alvo, codigo, estado, alterado_por=administrador)
