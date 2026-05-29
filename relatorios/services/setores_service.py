import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from relatorios.models import (
    OrigemSetorUsuario,
    PerfilUsuario,
    StatusImportacaoSetor,
    Tecnico,
    UsuarioSetorImportado,
    normalizar_nome_pessoa,
)


logger = logging.getLogger(__name__)


def nome_usuario_normalizado(user):
    partes = [getattr(user, "first_name", ""), getattr(user, "last_name", "")]
    nome = " ".join(p for p in partes if p).strip() or getattr(user, "username", "")
    return normalizar_nome_pessoa(nome)


def nome_tecnico_normalizado(tecnico):
    return normalizar_nome_pessoa(getattr(tecnico, "nome", ""))


@dataclass
class ResultadoAplicacaoSetor:
    aplicado: bool = False
    motivo: str = ""
    importacao: UsuarioSetorImportado | None = None


def sincronizar_setor_tecnico_por_usuario(user, perfil=None, origem=None, atualizado_por=None):
    perfil = perfil or getattr(user, "perfil_usuario", None)
    if not perfil or not perfil.setor_id:
        return None

    candidatos = Tecnico.objects.none()
    if user.email:
        candidatos = Tecnico.objects.filter(email__iexact=user.email)
    if not candidatos.exists():
        nome_norm = nome_usuario_normalizado(user)
        candidatos = [
            tecnico
            for tecnico in Tecnico.objects.filter(ativo=True).only("id", "nome", "email")
            if nome_tecnico_normalizado(tecnico) == nome_norm
        ]
    else:
        candidatos = list(candidatos)

    if len(candidatos) != 1:
        return None

    tecnico = candidatos[0]
    tecnico.setor = perfil.setor
    tecnico.funcao_setor = perfil.funcao_setor
    tecnico.setor_confirmado = perfil.setor_confirmado
    tecnico.setor_origem = origem or perfil.setor_origem
    tecnico.setor_atualizado_em = timezone.now()
    tecnico.setor_atualizado_por = atualizado_por
    tecnico.save(
        update_fields=[
            "setor",
            "funcao_setor",
            "setor_confirmado",
            "setor_origem",
            "setor_atualizado_em",
            "setor_atualizado_por",
        ]
    )
    return tecnico


def aplicar_setor_importado_para_usuario(user, *, sobrescrever=False):
    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=user)
    if perfil.setor_confirmado and not sobrescrever:
        return ResultadoAplicacaoSetor(False, "perfil_ja_confirmado")

    nome_norm = nome_usuario_normalizado(user)
    if not nome_norm:
        return ResultadoAplicacaoSetor(False, "usuario_sem_nome")

    importacoes = list(
        UsuarioSetorImportado.objects.select_related("setor")
        .filter(ativo=True, nome_normalizado=nome_norm)
        .order_by("id")
    )
    if len(importacoes) != 1:
        if len(importacoes) > 1:
            UsuarioSetorImportado.objects.filter(pk__in=[i.pk for i in importacoes]).update(
                status=StatusImportacaoSetor.AMBIGUO,
                observacao="Mais de um registro oficial com o mesmo nome normalizado.",
            )
        return ResultadoAplicacaoSetor(False, "sem_match_unico")

    importacao = importacoes[0]
    agora = timezone.now()
    with transaction.atomic():
        perfil = PerfilUsuario.objects.select_for_update().get(pk=perfil.pk)
        if perfil.setor_confirmado and not sobrescrever:
            return ResultadoAplicacaoSetor(False, "perfil_ja_confirmado", importacao)
        perfil.setor = importacao.setor
        perfil.funcao_setor = importacao.funcao
        perfil.setor_confirmado = True
        perfil.setor_origem = OrigemSetorUsuario.IMPORTACAO
        perfil.setor_atualizado_em = agora
        perfil.setor_atualizado_por = None
        perfil.save(
            update_fields=[
                "setor",
                "funcao_setor",
                "setor_confirmado",
                "setor_origem",
                "setor_atualizado_em",
                "setor_atualizado_por",
                "atualizado_em",
            ]
        )
        tecnico = sincronizar_setor_tecnico_por_usuario(
            user,
            perfil,
            origem=OrigemSetorUsuario.IMPORTACAO,
            atualizado_por=None,
        )
        importacao.usuario_vinculado = user
        if tecnico:
            importacao.tecnico_vinculado = tecnico
        importacao.status = StatusImportacaoSetor.APLICADO
        importacao.aplicado_em = agora
        importacao.observacao = "Aplicado automaticamente por match exato de nome."
        importacao.save(
            update_fields=[
                "usuario_vinculado",
                "tecnico_vinculado",
                "status",
                "aplicado_em",
                "observacao",
                "atualizado_em",
            ]
        )

    logger.info(
        "Setor aplicado automaticamente para usuario %s via importacao %s.",
        user.username,
        importacao.pk,
    )
    return ResultadoAplicacaoSetor(True, "aplicado", importacao)


def aplicar_setor_manual_perfil(user, *, setor, funcao_setor="", atualizado_por=None, origem=OrigemSetorUsuario.USUARIO):
    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=user)
    perfil.setor = setor
    perfil.funcao_setor = (funcao_setor or "").strip()
    perfil.setor_confirmado = True
    perfil.setor_origem = origem
    perfil.setor_atualizado_em = timezone.now()
    perfil.setor_atualizado_por = atualizado_por
    perfil.save(
        update_fields=[
            "setor",
            "funcao_setor",
            "setor_confirmado",
            "setor_origem",
            "setor_atualizado_em",
            "setor_atualizado_por",
            "atualizado_em",
        ]
    )
    sincronizar_setor_tecnico_por_usuario(
        user,
        perfil,
        origem=origem,
        atualizado_por=atualizado_por,
    )
    logger.info(
        "Setor confirmado manualmente para usuario %s. setor_id=%s origem=%s",
        user.username,
        setor.pk if setor else None,
        origem,
    )
    return perfil


def garantir_tecnico_para_usuario(user):
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email:
        return None

    nome = (getattr(user, "get_full_name", lambda: "")() or "").strip() or getattr(user, "username", "")
    if not nome:
        return None

    perfil = getattr(user, "perfil_usuario", None)
    tecnico = Tecnico.objects.filter(email__iexact=email).first()
    criado = tecnico is None
    if criado:
        tecnico = Tecnico(email=email, nome=nome, ativo=getattr(user, "is_active", True))

    alterado = criado
    if tecnico.nome != nome:
        tecnico.nome = nome
        alterado = True
    if tecnico.ativo != bool(getattr(user, "is_active", True)):
        tecnico.ativo = bool(getattr(user, "is_active", True))
        alterado = True
    if perfil and perfil.setor_id and (not tecnico.setor_id or perfil.setor_confirmado):
        tecnico.setor = perfil.setor
        tecnico.funcao_setor = perfil.funcao_setor
        tecnico.setor_confirmado = perfil.setor_confirmado
        tecnico.setor_origem = perfil.setor_origem
        tecnico.setor_atualizado_em = perfil.setor_atualizado_em or timezone.now()
        tecnico.setor_atualizado_por = perfil.setor_atualizado_por
        alterado = True

    if alterado:
        tecnico.save()
        logger.info(
            "Tecnico %s para usuario AD %s.",
            "criado" if criado else "atualizado",
            getattr(user, "username", ""),
        )
    return tecnico


def usuarios_por_nome_normalizado(nome_normalizado):
    User = get_user_model()
    return [
        usuario
        for usuario in User.objects.filter(is_active=True)
        if nome_usuario_normalizado(usuario) == nome_normalizado
    ]


def tecnicos_por_nome_normalizado(nome_normalizado):
    return [
        tecnico
        for tecnico in Tecnico.objects.filter(ativo=True)
        if nome_tecnico_normalizado(tecnico) == nome_normalizado
    ]
