import csv
import logging
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from relatorios.models import Cliente
from relatorios.services.clientes_sync_service import _somente_digitos
from relatorios.services.clientes_valor_km_service import normalizar_valor_km


logger = logging.getLogger("relatorios.importacao_valor_km")

TERMOS_REMOVER = {
    "ltda",
    "eireli",
    "sa",
    "s/a",
    "me",
    "epp",
    "cia",
    "companhia",
    "comercio",
    "industria",
    "de",
    "da",
    "do",
    "dos",
    "das",
}

SAIDA_DIR = Path("imports/valor_km/saida")
MAPEAMENTO_PADRAO = Path("imports/valor_km/mapeamento_valor_km_clientes.csv")


@dataclass
class CandidatoCliente:
    cliente: Cliente
    score: int
    nome_base: str


def normalizar_nome_cliente(nome):
    texto = unicodedata.normalize("NFKD", str(nome or "").lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace("s/a", " sa ")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    partes = [parte for parte in texto.split() if parte and parte not in TERMOS_REMOVER]
    return " ".join(partes)


def _score(a, b):
    if not a or not b:
        return 0
    if a == b:
        return 100
    return round(SequenceMatcher(None, a, b).ratio() * 100)


def _detectar_dialeto(caminho):
    amostra = caminho.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;")
    except csv.Error:
        dialect = csv.excel()
        dialect.delimiter = ";" if amostra.count(";") > amostra.count(",") else ","
        return dialect


def _ler_csv(caminho, limite=None):
    dialect = _detectar_dialeto(caminho)
    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        reader = csv.DictReader(arquivo, dialect=dialect)
        for idx, linha in enumerate(reader, start=2):
            if limite and idx - 1 > limite:
                break
            yield idx, {str(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()}


def _cliente_label(cliente):
    return getattr(cliente, "nome_exibicao", None) or cliente.nome


def _indexar_clientes(incluir_inativos=False):
    qs = Cliente.objects.all()
    if not incluir_inativos:
        qs = qs.filter(ativo=True)
    clientes = list(qs)
    por_cnpj = {}
    nomes = []
    for cliente in clientes:
        cnpj = _somente_digitos(cliente.cnpj_cpf)
        if cnpj:
            por_cnpj[cnpj] = cliente
        for nome in {cliente.nome, cliente.nome_fantasia, cliente.razao_social, _cliente_label(cliente)}:
            normalizado = normalizar_nome_cliente(nome)
            if normalizado:
                nomes.append((normalizado, cliente, nome))
    return por_cnpj, nomes


def _melhores_candidatos(nome_csv, nomes_indexados):
    nome_norm = normalizar_nome_cliente(nome_csv)
    candidatos_por_cliente = {}
    for nome_base_norm, cliente, nome_base in nomes_indexados:
        score = _score(nome_norm, nome_base_norm)
        atual = candidatos_por_cliente.get(cliente.pk)
        if atual is None or score > atual.score:
            candidatos_por_cliente[cliente.pk] = CandidatoCliente(cliente, score, nome_base)
    return sorted(candidatos_por_cliente.values(), key=lambda item: item.score, reverse=True)


def _formatar_candidatos(candidatos):
    return " | ".join(
        f"#{c.cliente.pk} {_cliente_label(c.cliente)} ({c.score})" for c in candidatos[:5]
    )


def _linha_base(cliente_csv, valor_km_csv):
    return {
        "cliente_csv": cliente_csv,
        "valor_km_csv": valor_km_csv,
    }


def _match_cliente(linha, por_cnpj, nomes_indexados, threshold_auto, threshold_pendente):
    cliente_csv = linha.get("cliente") or linha.get("nome") or linha.get("nome_cliente") or ""
    cnpj = _somente_digitos(linha.get("cnpj_cpf"))
    if cnpj and cnpj in por_cnpj:
        return "MATCH_AUTOMATICO", por_cnpj[cnpj], 100, [], "CNPJ/CPF"
    if not cliente_csv:
        return "ERRO", None, 0, [], "Linha sem cliente."

    candidatos = _melhores_candidatos(cliente_csv, nomes_indexados)
    if not candidatos:
        return "NAO_ENCONTRADO", None, 0, [], "Nenhum cliente local disponivel."

    topo = candidatos[0]
    fortes = [c for c in candidatos if c.score >= threshold_auto]
    if topo.score == 100 and len([c for c in candidatos if c.score == 100]) == 1:
        return "MATCH_AUTOMATICO", topo.cliente, topo.score, candidatos, "Match exato por nome normalizado."
    if topo.score >= threshold_auto and len(fortes) == 1:
        return "MATCH_AUTOMATICO", topo.cliente, topo.score, candidatos, "Match fuzzy de alta confianca."
    if topo.score >= threshold_pendente:
        return "PENDENTE_REVISAO", topo.cliente, topo.score, candidatos, "Match precisa de revisao manual."
    return "NAO_ENCONTRADO", None, topo.score, candidatos, "Nenhum candidato atingiu o limite minimo."


def _ler_mapeamento(caminho):
    if not caminho or not caminho.exists():
        return {}
    mapeamento = {}
    for _idx, linha in _ler_csv(caminho):
        chave = normalizar_nome_cliente(linha.get("cliente_csv") or linha.get("cliente") or "")
        if not chave:
            continue
        mapeamento[chave] = {
            "cliente_id": linha.get("cliente_id"),
            "valor_km": linha.get("valor_km"),
        }
    return mapeamento


def _validar_valor(valor_raw):
    valor = normalizar_valor_km(valor_raw)
    aviso = ""
    if valor > Decimal("10.00"):
        raise ValueError("valor_km acima de 10,00 exige revisao antes da importacao")
    if valor > Decimal("5.00"):
        aviso = "Valor KM acima de 5,00. Conferir se esta correto."
    return valor, aviso


def _escrever_csv(caminho, fieldnames, rows):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class Command(BaseCommand):
    help = "Importacao assistida de valor_km local dos clientes a partir de CSV legado."

    def add_arguments(self, parser):
        parser.add_argument("arquivo_csv", help="CSV com cliente,valor_km e opcionalmente cnpj_cpf.")
        parser.add_argument("--dry-run", action="store_true", help="Processa sem gravar.")
        parser.add_argument("--confirmar", action="store_true", help="Aplica matches automaticos/manuais.")
        parser.add_argument("--limite", type=int, default=None, help="Processa apenas N linhas.")
        parser.add_argument("--threshold-auto", type=int, default=95)
        parser.add_argument("--threshold-pendente", type=int, default=85)
        parser.add_argument("--sobrescrever", action="store_true", help="Permite alterar valor_km existente.")
        parser.add_argument("--nao-sobrescrever", dest="sobrescrever", action="store_false")
        parser.add_argument("--incluir-inativos", action="store_true", help="Permite atualizar clientes inativos.")
        parser.add_argument("--mapeamento", default=str(MAPEAMENTO_PADRAO), help="CSV opcional cliente_csv,cliente_id,valor_km.")

    def handle(self, *args, **options):
        caminho = Path(options["arquivo_csv"])
        if not caminho.exists():
            raise CommandError(f"Arquivo nao encontrado: {caminho}")
        if not options["dry_run"] and not options["confirmar"]:
            raise CommandError("Use --dry-run para testar ou --confirmar para gravar.")

        threshold_auto = int(options["threshold_auto"])
        threshold_pendente = int(options["threshold_pendente"])
        if threshold_pendente > threshold_auto:
            raise CommandError("--threshold-pendente deve ser menor ou igual ao --threshold-auto.")

        logger.info("Inicio importacao assistida valor_km arquivo=%s dry_run=%s", caminho, options["dry_run"])
        por_cnpj, nomes_indexados = _indexar_clientes(options["incluir_inativos"])
        mapeamento = _ler_mapeamento(Path(options["mapeamento"])) if options.get("mapeamento") else {}

        matches = []
        pendentes = []
        nao_encontrados = []
        resultado = []
        totais = {
            "lidos": 0,
            "automaticos": 0,
            "manuais": 0,
            "pendentes": 0,
            "nao_encontrados": 0,
            "ja_possui": 0,
            "atualizados": 0,
            "erros": 0,
            "warnings": 0,
        }

        for linha_num, linha in _ler_csv(caminho, options["limite"]):
            totais["lidos"] += 1
            cliente_csv = linha.get("cliente") or linha.get("nome") or linha.get("nome_cliente") or ""
            valor_km_csv = linha.get("valor_km") or ""
            base = _linha_base(cliente_csv, valor_km_csv)
            valor_raw = valor_km_csv
            status_origem = ""

            try:
                chave_manual = normalizar_nome_cliente(cliente_csv)
                if chave_manual in mapeamento:
                    manual = mapeamento[chave_manual]
                    cliente = Cliente.objects.filter(pk=manual["cliente_id"]).first()
                    if not cliente or (not options["incluir_inativos"] and not cliente.ativo):
                        raise ValueError("mapeamento manual aponta para cliente inexistente ou inativo")
                    valor_raw = manual.get("valor_km") or valor_raw
                    status = "MATCH_MANUAL"
                    score = 100
                    candidatos = []
                    observacao = "Mapeamento manual."
                else:
                    status, cliente, score, candidatos, observacao = _match_cliente(
                        linha,
                        por_cnpj,
                        nomes_indexados,
                        threshold_auto,
                        threshold_pendente,
                    )

                valor, aviso_valor = _validar_valor(valor_raw)
                if aviso_valor:
                    totais["warnings"] += 1
                    observacao = f"{observacao} {aviso_valor}".strip()

                if status == "PENDENTE_REVISAO":
                    totais["pendentes"] += 1
                    pendentes.append(
                        {
                            **base,
                            "melhor_candidato_id": getattr(cliente, "pk", ""),
                            "melhor_candidato_nome": _cliente_label(cliente) if cliente else "",
                            "score": score,
                            "candidatos_sugeridos": _formatar_candidatos(candidatos),
                            "observacao": observacao,
                        }
                    )
                    resultado.append({**base, "cliente_id": getattr(cliente, "pk", ""), "valor_anterior": "", "valor_novo": str(valor), "status": status, "mensagem": observacao})
                    continue

                if status == "NAO_ENCONTRADO":
                    totais["nao_encontrados"] += 1
                    nao_encontrados.append({**base, "observacao": observacao})
                    resultado.append({**base, "cliente_id": "", "valor_anterior": "", "valor_novo": str(valor), "status": status, "mensagem": observacao})
                    continue

                if status == "ERRO" or cliente is None:
                    raise ValueError(observacao or "cliente nao identificado")

                valor_anterior = cliente.valor_km
                if valor_anterior and valor_anterior > 0 and not options["sobrescrever"]:
                    totais["ja_possui"] += 1
                    resultado.append(
                        {
                            **base,
                            "cliente_id": cliente.pk,
                            "valor_anterior": str(valor_anterior),
                            "valor_novo": str(valor),
                            "status": "JA_POSSUI_VALOR_KM",
                            "mensagem": "Cliente ja possui valor_km; use --sobrescrever para alterar.",
                        }
                    )
                    continue

                acao = "DRY_RUN" if options["dry_run"] else "ATUALIZADO"
                if not options["dry_run"]:
                    cliente.valor_km = valor
                    cliente.valor_km_atualizado_em = timezone.now()
                    cliente.valor_km_observacao = "Importacao legado valor_km"
                    cliente.save(update_fields=["valor_km", "valor_km_atualizado_em", "valor_km_observacao"])
                    totais["atualizados"] += 1

                if status == "MATCH_MANUAL":
                    totais["manuais"] += 1
                else:
                    totais["automaticos"] += 1

                matches.append(
                    {
                        **base,
                        "cliente_id": cliente.pk,
                        "cliente_sistema": _cliente_label(cliente),
                        "razao_social": cliente.razao_social,
                        "nome_fantasia": cliente.nome_fantasia,
                        "cnpj_cpf": cliente.cnpj_cpf,
                        "score": score,
                        "acao": status,
                    }
                )
                resultado.append(
                    {
                        **base,
                        "cliente_id": cliente.pk,
                        "valor_anterior": str(valor_anterior or ""),
                        "valor_novo": str(valor),
                        "status": acao if status != "MATCH_MANUAL" else f"{acao}_MANUAL",
                        "mensagem": observacao,
                    }
                )
            except Exception as exc:
                totais["erros"] += 1
                mensagem = f"linha {linha_num}: {exc}"
                resultado.append({**base, "cliente_id": "", "valor_anterior": "", "valor_novo": valor_raw, "status": "ERRO", "mensagem": mensagem})
                nao_encontrados.append({**base, "observacao": mensagem})

        _escrever_csv(
            SAIDA_DIR / "valor_km_matches_automaticos.csv",
            ["cliente_csv", "valor_km_csv", "cliente_id", "cliente_sistema", "razao_social", "nome_fantasia", "cnpj_cpf", "score", "acao"],
            matches,
        )
        _escrever_csv(
            SAIDA_DIR / "valor_km_pendentes_revisao.csv",
            ["cliente_csv", "valor_km_csv", "melhor_candidato_id", "melhor_candidato_nome", "score", "candidatos_sugeridos", "observacao"],
            pendentes,
        )
        _escrever_csv(
            SAIDA_DIR / "valor_km_nao_encontrados.csv",
            ["cliente_csv", "valor_km_csv", "observacao"],
            nao_encontrados,
        )
        _escrever_csv(
            SAIDA_DIR / "valor_km_resultado_importacao.csv",
            ["cliente_csv", "cliente_id", "valor_anterior", "valor_novo", "status", "mensagem"],
            resultado,
        )

        logger.info("Fim importacao valor_km totais=%s", totais)
        self.stdout.write(
            self.style.SUCCESS(
                "Importacao assistida concluida: "
                + ", ".join(f"{chave}={valor}" for chave, valor in totais.items())
                + f", saida={SAIDA_DIR}"
            )
        )
