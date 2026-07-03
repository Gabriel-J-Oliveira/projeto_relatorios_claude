import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timezone as datetime_timezone

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from relatorios.models import Cliente
from relatorios.services.km_financeiro_service import (
    filtro_empresas_internas_grupo_q,
    valor_km_cliente_contratual,
)
from relatorios.services.clientes_api_service import buscar_clientes_api


logger = logging.getLogger(__name__)


@dataclass
class ResultadoSincronizacaoClientes:
    total_recebidos: int = 0
    criados: int = 0
    criados_sem_valor_km: int = 0
    atualizados: int = 0
    sem_alteracao: int = 0
    inativados: int = 0
    pendentes_valor_km: int = 0
    erros: int = 0
    detalhes_erros: list[str] = field(default_factory=list)


def _somente_digitos(valor):
    return re.sub(r"\D+", "", str(valor or ""))


def _texto(valor, max_length=None):
    texto = str(valor or "").strip()
    if max_length and len(texto) > max_length:
        return texto[:max_length]
    return texto


def _bool_api(valor, default=True):
    if valor is None:
        return default
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in {"1", "true", "t", "yes", "sim", "ativo"}


def _parse_api_datetime(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    dt = parse_datetime(texto)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=datetime_timezone.utc)
    return dt


def calcular_hash_cliente(dados):
    payload = {
        chave: dados.get(chave)
        for chave in sorted(
            [
                "cnpj_cpf",
                "razao_social",
                "nome_fantasia",
                "cep",
                "uf",
                "cidade",
                "logradouro",
                "numero",
                "bairro",
                "complemento",
                "telefone",
                "ativo",
                "api_created_at",
                "api_updated_at",
            ]
        )
    }
    texto = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def normalizar_cliente_api(payload):
    if not isinstance(payload, dict):
        raise ValueError("registro nao e um objeto JSON")

    cnpj_cpf = _somente_digitos(payload.get("cnpj_cpf"))
    if not cnpj_cpf:
        raise ValueError("cnpj_cpf ausente")

    razao_social = _texto(payload.get("company"), 200)
    nome_fantasia = _texto(payload.get("name"), 200)
    nome = nome_fantasia or razao_social or cnpj_cpf
    dados = {
        "cnpj_cpf": cnpj_cpf,
        "razao_social": razao_social,
        "nome_fantasia": nome_fantasia,
        "nome": _texto(nome, 200),
        "cep": _somente_digitos(payload.get("cep"))[:12],
        "uf": _texto(payload.get("uf"), 2).upper(),
        "cidade": _texto(payload.get("city"), 100),
        "logradouro": _texto(payload.get("street"), 200),
        "numero": _texto(payload.get("number"), 30),
        "bairro": _texto(payload.get("district"), 100),
        "complemento": _texto(payload.get("complement"), 150),
        "telefone": _somente_digitos(payload.get("phone"))[:20],
        "ativo": _bool_api(payload.get("is_active"), default=True),
        "api_created_at": _parse_api_datetime(payload.get("created_at")),
        "api_updated_at": _parse_api_datetime(payload.get("updated_at")),
    }
    dados["hash_dados_api"] = calcular_hash_cliente(dados)
    return dados


def _campos_api_para_update():
    return [
        "nome",
        "cnpj_cpf",
        "razao_social",
        "nome_fantasia",
        "cep",
        "uf",
        "cidade",
        "logradouro",
        "numero",
        "bairro",
        "complemento",
        "telefone",
        "ativo",
        "api_created_at",
        "api_updated_at",
        "sincronizado_em",
        "origem_api",
        "hash_dados_api",
        "valor_km_pendente_api_novo",
    ]


def criar_ou_atualizar_cliente(dados, dry_run=False, force=False):
    cliente = Cliente.objects.filter(cnpj_cpf=dados["cnpj_cpf"]).first()
    agora = timezone.now()

    if cliente is None:
        if dry_run:
            return "criado", None
        cliente = Cliente(
            **{
                campo: dados[campo]
                for campo in [
                    "nome",
                    "cnpj_cpf",
                    "razao_social",
                    "nome_fantasia",
                    "cep",
                    "uf",
                    "cidade",
                    "logradouro",
                    "numero",
                    "bairro",
                    "complemento",
                    "telefone",
                    "ativo",
                    "api_created_at",
                    "api_updated_at",
                    "hash_dados_api",
                ]
            },
            origem_api=True,
            sincronizado_em=agora,
            valor_km_pendente_api_novo=True,
        )
        cliente.save()
        logger.info("Cliente criado pela API sem valor_km local. cliente_id=%s cnpj_cpf=%s", cliente.pk, cliente.cnpj_cpf)
        return "criado", cliente

    if not force and cliente.hash_dados_api == dados["hash_dados_api"]:
        return "sem_alteracao", cliente

    status = "inativado" if cliente.ativo and not dados["ativo"] else "atualizado"
    if dry_run:
        return status, cliente

    for campo, valor in dados.items():
        setattr(cliente, campo, valor)
    cliente.origem_api = True
    cliente.sincronizado_em = agora
    cliente.save(update_fields=_campos_api_para_update())
    return status, cliente


def sincronizar_clientes(dry_run=False, limit=None, force=False, verbose=False):
    inicio = timezone.now()
    registros = buscar_clientes_api()
    if limit:
        registros = registros[:limit]

    resultado = ResultadoSincronizacaoClientes(total_recebidos=len(registros))
    logger.info(
        "Sincronizacao de clientes iniciada. total=%s dry_run=%s force=%s",
        len(registros),
        dry_run,
        force,
    )

    for indice, payload in enumerate(registros, start=1):
        try:
            dados = normalizar_cliente_api(payload)
            with transaction.atomic():
                status, cliente = criar_ou_atualizar_cliente(
                    dados,
                    dry_run=dry_run,
                    force=force,
                )
            if status == "criado":
                resultado.criados += 1
                if cliente is None or valor_km_cliente_contratual(cliente) is None:
                    resultado.criados_sem_valor_km += 1
            elif status == "atualizado":
                resultado.atualizados += 1
            elif status == "inativado":
                resultado.inativados += 1
            else:
                resultado.sem_alteracao += 1
            if verbose:
                logger.info(
                    "Cliente API processado. status=%s cnpj_cpf=%s cliente_id=%s",
                    status,
                    dados["cnpj_cpf"],
                    getattr(cliente, "pk", None),
                )
        except Exception as exc:
            resultado.erros += 1
            identificador = payload.get("cnpj_cpf") if isinstance(payload, dict) else f"linha {indice}"
            detalhe = f"{identificador}: {exc}"
            resultado.detalhes_erros.append(detalhe)
            logger.warning("Cliente API ignorado: %s", detalhe)

    resultado.pendentes_valor_km = (
        Cliente.objects.filter(ativo=True)
        .filter(Q(valor_km__isnull=True) | Q(valor_km__lte=0))
        .exclude(filtro_empresas_internas_grupo_q())
        .count()
    )
    duracao = (timezone.now() - inicio).total_seconds()
    logger.info(
        "Sincronizacao de clientes finalizada em %.2fs. recebidos=%s criados=%s criados_sem_valor_km=%s atualizados=%s sem_alteracao=%s inativados=%s pendentes_valor_km=%s erros=%s",
        duracao,
        resultado.total_recebidos,
        resultado.criados,
        resultado.criados_sem_valor_km,
        resultado.atualizados,
        resultado.sem_alteracao,
        resultado.inativados,
        resultado.pendentes_valor_km,
        resultado.erros,
    )
    return resultado
