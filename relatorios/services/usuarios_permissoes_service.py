from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
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
    CodigoPermissao.ERP_SOMENTE_LEITURA_GLOBAL,
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
    (CategoriaPermissao.SISTEMA, "Sistema"),
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
    dependencias_pendentes_nomes: tuple[str, ...]


@dataclass(frozen=True)
class UsuarioReplicacaoLinha:
    usuario: object
    nome: str
    grupos: tuple[str, ...]


@dataclass(frozen=True)
class ReplicacaoAlteracaoPreview:
    usuario: object
    codigo: str
    nome: str
    estado_anterior: str
    estado_novo: str
    sensibilidade: str


@dataclass(frozen=True)
class ReplicacaoDestinoPreview:
    usuario: object
    alteracoes: tuple[ReplicacaoAlteracaoPreview, ...]
    iguais: int
    erros: tuple[str, ...]


@dataclass(frozen=True)
class ReplicacaoPermissoesPreview:
    fonte: object
    modo: str
    destinos: tuple[ReplicacaoDestinoPreview, ...]
    codigos: tuple[str, ...]
    total_destinos: int
    total_alteracoes: int
    total_iguais: int
    criados: int
    alterados: int
    removidos: int
    criticas: int
    erros: tuple[str, ...]


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
        dependencias_pendentes_nomes = []
        for dependencia in dependencias:
            permitido, _origem = _estado_efetivo_basico(usuario, dependencia, overrides)
            if not permitido:
                dependencias_pendentes.append(dependencia)
                permissao_dependencia = obter_permissao(dependencia)
                dependencias_pendentes_nomes.append(
                    permissao_dependencia.nome if permissao_dependencia else dependencia
                )
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
                dependencias_pendentes_nomes=tuple(dependencias_pendentes_nomes),
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


def usuarios_disponiveis_replicacao(usuario_fonte):
    User = get_user_model()
    usuarios = (
        User.objects.exclude(pk=usuario_fonte.pk)
        .prefetch_related("groups")
        .order_by("first_name", "username")
    )
    linhas = []
    for usuario in usuarios:
        if usuario_tem_full_access_erp(usuario):
            continue
        linhas.append(
            UsuarioReplicacaoLinha(
                usuario=usuario,
                nome=usuario.get_full_name() or usuario.username,
                grupos=tuple(grupos_usuario(usuario)),
            )
        )
    return linhas


def permissoes_replicacao_agrupadas():
    grupos = []
    permissoes = [permissao for permissao in listar_permissoes() if permissao.codigo in CODIGOS_CENTRAL_USUARIOS_V1]
    for categoria, label in CATEGORIAS_UI:
        itens = [permissao for permissao in permissoes if permissao.categoria == categoria]
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


def _normalizar_ids_destino(ids):
    normalizados = []
    for valor in ids:
        try:
            normalizados.append(int(valor))
        except (TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(normalizados))


def _normalizar_codigos_replicacao(codigos):
    permitidos = set(CODIGOS_CENTRAL_USUARIOS_V1)
    normalizados = []
    for codigo in codigos:
        codigo = (codigo or "").strip()
        if codigo in permitidos and codigo not in normalizados:
            normalizados.append(codigo)
    return tuple(normalizados)


def _estado_label(estado):
    if estado == EstadoPermissaoUsuario.PERMITIR:
        return "PERMITIR"
    if estado == EstadoPermissaoUsuario.NEGAR:
        return "NEGAR"
    return "HERDAR"


def _estado_tecnico(overrides, codigo):
    return overrides.get(codigo, EstadoHistoricoPermissao.HERDAR)


def _carregar_usuarios_destino(ids):
    User = get_user_model()
    usuarios = (
        User.objects.filter(pk__in=ids)
        .prefetch_related("groups", "permissoes_overrides")
        .order_by("first_name", "username")
    )
    por_id = {usuario.pk: usuario for usuario in usuarios}
    return [por_id[pk] for pk in ids if pk in por_id]


def _validar_dependencias_estado_final(usuario, overrides_finais, codigos):
    erros = []
    for codigo in codigos:
        if overrides_finais.get(codigo) != EstadoPermissaoUsuario.PERMITIR:
            continue
        pendentes = []
        for dependencia in DEPENDENCIAS_PERMISSOES.get(codigo, ()):
            permitido, _origem = _estado_efetivo_basico(usuario, dependencia, overrides_finais)
            if not permitido:
                permissao_dependencia = obter_permissao(dependencia)
                pendentes.append(permissao_dependencia.nome if permissao_dependencia else dependencia)
        if pendentes:
            permissao = obter_permissao(codigo)
            erros.append(f"{permissao.nome if permissao else codigo} requer " + ", ".join(pendentes) + ".")
    return erros


def preparar_preview_replicacao_permissoes(
    *,
    administrador,
    usuario_fonte,
    destino_ids,
    codigos,
    modo,
):
    if not usuario_pode_acessar_central_permissoes(administrador):
        raise PermissaoCentralError("Usuario sem permissao para gerenciar permissoes.")
    if usuario_tem_full_access_erp(usuario_fonte):
        raise PermissaoCentralError("Full Access protegido nao pode ser fonte de replicacao.")
    if modo not in {"overrides", "exato"}:
        raise PermissaoCentralError("Modo de replicacao invalido.")

    destino_ids = _normalizar_ids_destino(destino_ids)
    codigos = _normalizar_codigos_replicacao(codigos or CODIGOS_CENTRAL_USUARIOS_V1)
    if not destino_ids:
        raise PermissaoCentralError("Selecione ao menos um usuario de destino.")
    if not codigos:
        raise PermissaoCentralError("Selecione ao menos uma permissao.")

    usuario_fonte = (
        get_user_model()
        .objects.prefetch_related("permissoes_overrides")
        .get(pk=usuario_fonte.pk)
    )
    source_overrides = _overrides_map(usuario_fonte)
    destinos = _carregar_usuarios_destino(destino_ids)
    if not destinos:
        raise PermissaoCentralError("Nenhum usuario de destino valido foi selecionado.")

    previews = []
    total_alteracoes = 0
    total_iguais = 0
    criados = 0
    alterados = 0
    removidos = 0
    criticas = 0
    erros_gerais = []

    for destino in destinos:
        erros = []
        if destino.pk == usuario_fonte.pk:
            erros.append("O usuario fonte nao pode ser destino.")
        if usuario_tem_full_access_erp(destino):
            erros.append("Full Access protegido nao pode receber overrides.")

        destino_overrides = _overrides_map(destino)
        overrides_finais = dict(destino_overrides)
        alteracoes = []
        iguais = 0

        for codigo in codigos:
            estado_fonte = _estado_tecnico(source_overrides, codigo)
            if modo == "overrides" and estado_fonte == EstadoHistoricoPermissao.HERDAR:
                continue
            estado_atual = _estado_tecnico(destino_overrides, codigo)
            estado_novo = estado_fonte
            if estado_novo == EstadoHistoricoPermissao.HERDAR:
                overrides_finais.pop(codigo, None)
            else:
                overrides_finais[codigo] = estado_novo

            if estado_atual == estado_novo:
                iguais += 1
                continue

            permissao = obter_permissao(codigo)
            alteracoes.append(
                ReplicacaoAlteracaoPreview(
                    usuario=destino,
                    codigo=codigo,
                    nome=permissao.nome if permissao else codigo,
                    estado_anterior=_estado_label(estado_atual),
                    estado_novo=_estado_label(estado_novo),
                    sensibilidade=getattr(permissao, "sensibilidade", ""),
                )
            )
            if estado_atual == EstadoHistoricoPermissao.HERDAR and estado_novo != EstadoHistoricoPermissao.HERDAR:
                criados += 1
            elif estado_atual != EstadoHistoricoPermissao.HERDAR and estado_novo == EstadoHistoricoPermissao.HERDAR:
                removidos += 1
            else:
                alterados += 1
            if getattr(permissao, "sensibilidade", "") == SensibilidadePermissao.CRITICA:
                criticas += 1

        erros.extend(_validar_dependencias_estado_final(destino, overrides_finais, codigos))
        total_alteracoes += len(alteracoes)
        total_iguais += iguais
        previews.append(
            ReplicacaoDestinoPreview(
                usuario=destino,
                alteracoes=tuple(alteracoes),
                iguais=iguais,
                erros=tuple(erros),
            )
        )
        erros_gerais.extend([f"{destino.username}: {erro}" for erro in erros])

    return ReplicacaoPermissoesPreview(
        fonte=usuario_fonte,
        modo=modo,
        destinos=tuple(previews),
        codigos=codigos,
        total_destinos=len(destinos),
        total_alteracoes=total_alteracoes,
        total_iguais=total_iguais,
        criados=criados,
        alterados=alterados,
        removidos=removidos,
        criticas=criticas,
        erros=tuple(erros_gerais),
    )


def aplicar_replicacao_permissoes(*, preview, administrador):
    if preview.erros:
        raise PermissaoCentralError("Corrija os conflitos do preview antes de aplicar.")
    aplicadas = 0
    ordem_catalogo = {codigo: indice for indice, codigo in enumerate(CODIGOS_CENTRAL_USUARIOS_V1)}
    with transaction.atomic():
        for destino_preview in preview.destinos:
            alteracoes = sorted(
                destino_preview.alteracoes,
                key=lambda item: ordem_catalogo.get(item.codigo, len(ordem_catalogo)),
            )
            for alteracao in alteracoes:
                salvar_override_central(
                    administrador=administrador,
                    usuario_alvo=destino_preview.usuario,
                    codigo=alteracao.codigo,
                    estado=alteracao.estado_novo.lower(),
                )
                aplicadas += 1
    return aplicadas
