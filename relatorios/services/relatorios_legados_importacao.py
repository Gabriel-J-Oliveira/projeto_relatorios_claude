import csv
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from relatorios.models import (
    Cliente,
    DespesaLegada,
    KmLegado,
    OrigemRelatorioLegado,
    RelatorioLegado,
    Tecnico,
    normalizar_nome_pessoa,
    normalizar_texto_busca,
)


logger = logging.getLogger("relatorios.importacao_legado")

TIPOS_DESPESA_LEGADO = {
    "1": "Hospedagem",
    "2": "Passagem",
    "3": "Transporte / Táxi / Uber",
    "4": "Alimentação",
    "5": "Estacionamento",
    "6": "Pedágio",
    "7": "Veículos",
    "8": "Pedágio",
}

LOCALIDADES = {"capital", "interior", "fronteira"}


@dataclass
class ResultadoImportacaoLegado:
    lidos: int = 0
    ignorados_vazios: int = 0
    criados: int = 0
    atualizados: int = 0
    sem_alteracao: int = 0
    despesas: int = 0
    kms: int = 0
    pendencias: int = 0
    erros: int = 0


def _limpar(valor):
    return str(valor or "").strip()


def _compactar(valor):
    return " ".join(_limpar(valor).split())


def parse_decimal(valor):
    texto = _limpar(valor)
    if not texto:
        return None
    texto = texto.replace("R$", "").replace(" ", "").strip()
    texto = re.sub(r"[^0-9,.\-]", "", texto)
    if not texto or texto in {"-", ",", "."}:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_data(valor):
    texto = _compactar(valor)
    if not texto:
        return None
    numero = parse_decimal(texto)
    if numero and numero >= Decimal("30000") and numero <= Decimal("90000"):
        return date(1899, 12, 30) + timedelta(days=int(numero))
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None


def parse_periodo(texto):
    texto = _compactar(texto)
    if not texto:
        return None, None
    datas = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", texto)
    if len(datas) >= 2:
        inicio = parse_data(datas[0])
        fim = parse_data(datas[-1])
        return inicio, fim or inicio
    if len(datas) == 1:
        data_unica = parse_data(datas[0])
        return data_unica, data_unica

    match = re.match(r"(\d{1,2})\s*a\s*(\d{1,2})/(\d{1,2})/(\d{4})", texto, flags=re.I)
    if match:
        dia_inicio, dia_fim, mes, ano = map(int, match.groups())
        try:
            return date(ano, mes, dia_inicio), date(ano, mes, dia_fim)
        except ValueError:
            return None, None

    data_unica = parse_data(texto)
    return data_unica, data_unica


def _cidade_uf(valor):
    texto = _compactar(valor)
    if " - " in texto:
        uf, cidade = texto.split(" - ", 1)
        return cidade.strip(), uf.strip()[:2].upper()
    return texto, ""


def _primeira_localidade(row):
    for index, valor in enumerate(row):
        norm = normalizar_texto_busca(valor)
        if norm in LOCALIDADES:
            return index, valor.strip().title()
    return None, ""


def _inferir_tail(row, localidade_index):
    if localidade_index is not None:
        start = max(0, localidade_index - 6)
        return start, row[start : localidade_index + 1]
    ultimos = [(i, _limpar(v)) for i, v in enumerate(row) if _limpar(v)]
    if not ultimos:
        return len(row), []
    start = max(24, ultimos[-1][0] - 9)
    return start, row[start : ultimos[-1][0] + 1]


def _inferir_km(tail):
    valores = [parse_decimal(item) for item in tail]
    valores = [valor for valor in valores if valor is not None]
    valor_km = next((valor for valor in valores if Decimal("0.50") <= valor <= Decimal("10.00")), None)
    candidatos_km = [valor for valor in valores if valor > Decimal("10.00")]
    km = candidatos_km[-1] if candidatos_km else None
    total = (km * valor_km).quantize(Decimal("0.01")) if km and valor_km else None
    return km, valor_km, total


def _despesas_linha(row, tail_start):
    despesas = []
    ordem = 1
    limite = min(tail_start, len(row))
    for start in range(24, limite, 6):
        grupo = list(row[start : start + 6])
        grupo += [""] * (6 - len(grupo))
        data_original, documento, descricao, valor, quantidade, tipo = [_compactar(item) for item in grupo[:6]]
        if not any([data_original, documento, descricao, valor, quantidade, tipo]):
            continue
        valor_decimal = parse_decimal(valor)
        if not descricao and valor_decimal is None:
            continue
        despesas.append(
            {
                "ordem": ordem,
                "data": parse_data(data_original),
                "data_original": data_original,
                "documento": documento,
                "descricao": descricao,
                "valor": valor_decimal or Decimal("0.00"),
                "quantidade": parse_decimal(quantidade),
                "tipo_codigo": tipo,
                "tipo_descricao": TIPOS_DESPESA_LEGADO.get(tipo, tipo or ""),
                "raw": {
                    "coluna_inicio": start + 1,
                    "data": data_original,
                    "documento": documento,
                    "descricao": descricao,
                    "valor": valor,
                    "quantidade": quantidade,
                    "tipo": tipo,
                },
            }
        )
        ordem += 1
    return despesas


def _resolver_cliente(nome):
    nome_norm = normalizar_texto_busca(nome)
    if not nome_norm:
        return None
    candidatos = [
        cliente
        for cliente in Cliente.objects.filter(ativo=True).only("id", "nome", "nome_fantasia", "razao_social")
        if nome_norm
        in {
            normalizar_texto_busca(cliente.nome),
            normalizar_texto_busca(cliente.nome_fantasia),
            normalizar_texto_busca(cliente.razao_social),
        }
    ][:2]
    return candidatos[0] if len(candidatos) == 1 else None


def _resolver_tecnico(nome):
    nome_norm = normalizar_nome_pessoa(nome)
    if not nome_norm:
        return None
    candidatos = list(Tecnico.objects.filter(ativo=True, nome__iexact=nome).order_by("nome")[:2])
    if len(candidatos) == 1:
        return candidatos[0]
    candidatos = [tec for tec in Tecnico.objects.filter(ativo=True) if normalizar_nome_pessoa(tec.nome) == nome_norm]
    return candidatos[0] if len(candidatos) == 1 else None


def parse_linha_legado(row, arquivo_origem, linha_numero):
    numero = _compactar(row[0] if len(row) > 0 else "")
    if not numero:
        return None
    campos_reais = [valor for idx, valor in enumerate(row) if idx > 0 and _limpar(valor)]
    if not campos_reais:
        return None

    localidade_index, localidade = _primeira_localidade(row)
    tail_start, tail = _inferir_tail(row, localidade_index)
    despesas = _despesas_linha(row, tail_start)
    cidade, uf = _cidade_uf(row[4] if len(row) > 4 else "")
    data_texto = _compactar(row[20] if len(row) > 20 else "")
    data_inicio, data_fim = parse_periodo(data_texto)
    km, valor_km, total_km_valor = _inferir_km(tail)
    total_despesas = sum((item["valor"] for item in despesas), Decimal("0.00")).quantize(Decimal("0.01"))
    total_geral = (total_despesas + (total_km_valor or Decimal("0.00"))).quantize(Decimal("0.01"))

    colaboradores = [_compactar(row[i]) for i in range(5, 10) if len(row) > i and _compactar(row[i])]
    diarias = [_compactar(row[i]) for i in range(10, 15) if len(row) > i and _compactar(row[i])]
    periodos = [_compactar(row[i]) for i in range(15, 20) if len(row) > i and _compactar(row[i])]

    return {
        "numero_original_legado": numero,
        "arquivo_origem_legado": Path(arquivo_origem).name,
        "linha_origem_legado": linha_numero,
        "escritorio": _compactar(row[1] if len(row) > 1 else ""),
        "tecnico_nome": _compactar(row[2] if len(row) > 2 else ""),
        "cliente_nome": _compactar(row[3] if len(row) > 3 else ""),
        "cidade": cidade,
        "uf": uf,
        "tipo_localidade": localidade,
        "colaboradores": colaboradores,
        "diarias": diarias,
        "periodos": periodos,
        "data_texto": data_texto,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "motivo": _compactar(row[21] if len(row) > 21 else ""),
        "gasto": _compactar(row[22] if len(row) > 22 else ""),
        "reembolso": _compactar(row[23] if len(row) > 23 else ""),
        "total_despesas": total_despesas,
        "total_km": km,
        "valor_km": valor_km,
        "total_km_valor": total_km_valor,
        "total_geral": total_geral,
        "despesas": despesas,
        "km_raw": {"tail_inicio_coluna": tail_start + 1, "valores": tail},
        "dados_legado_json": {"linha": row, "tail": tail},
    }


def importar_relatorios_legados_csv(caminho, *, confirmar=False, usuario=None, limite=None, substituir=False):
    path = Path(caminho)
    if not path.exists():
        raise FileNotFoundError(str(path))

    resultado = ResultadoImportacaoLegado()
    importador = None
    if usuario:
        User = get_user_model()
        importador = User.objects.filter(username=usuario).first()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        itens = []
        for linha_numero, row in enumerate(reader, start=2):
            if limite and resultado.lidos >= limite:
                break
            resultado.lidos += 1
            parsed = parse_linha_legado(row, path.name, linha_numero)
            if not parsed:
                resultado.ignorados_vazios += 1
                continue
            itens.append(parsed)

    if not confirmar:
        for item in itens:
            resultado.despesas += len(item["despesas"])
            if item["total_km"] or item["total_km_valor"]:
                resultado.kms += 1
        try:
            for item in itens:
                if not _resolver_cliente(item["cliente_nome"]) or not _resolver_tecnico(item["tecnico_nome"]):
                    resultado.pendencias += 1
        except (OperationalError, ProgrammingError) as exc:
            logger.warning("Dry-run legado sem avaliacao de vinculos por indisponibilidade do banco: %s", exc)
        return resultado

    with transaction.atomic():
        for item in itens:
            existente = RelatorioLegado.objects.filter(
                origem=OrigemRelatorioLegado.LEGADO_PLANILHA,
                numero_original_legado=item["numero_original_legado"],
            ).first()
            if existente and not substituir:
                resultado.sem_alteracao += 1
                continue

            cliente = _resolver_cliente(item["cliente_nome"])
            tecnico = _resolver_tecnico(item["tecnico_nome"])
            if not cliente or not tecnico:
                resultado.pendencias += 1

            defaults = {
                "is_legado": True,
                "is_historico_frio": True,
                "arquivo_origem_legado": item["arquivo_origem_legado"],
                "linha_origem_legado": item["linha_origem_legado"],
                "importado_por": importador,
                "observacao_legado": "Relatório importado da planilha antiga para consulta histórica.",
                "dados_legado_json": item["dados_legado_json"],
                "escritorio": item["escritorio"],
                "cliente_nome": item["cliente_nome"],
                "cliente_vinculado": cliente,
                "tecnico_nome": item["tecnico_nome"],
                "tecnico_nome_normalizado": normalizar_nome_pessoa(item["tecnico_nome"]),
                "tecnico_vinculado": tecnico,
                "cidade": item["cidade"],
                "uf": item["uf"],
                "tipo_localidade": item["tipo_localidade"],
                "colaboradores": item["colaboradores"],
                "diarias": item["diarias"],
                "periodos": item["periodos"],
                "data_texto": item["data_texto"],
                "data_inicio": item["data_inicio"],
                "data_fim": item["data_fim"],
                "motivo": item["motivo"],
                "gasto": item["gasto"],
                "reembolso": item["reembolso"],
                "total_despesas": item["total_despesas"],
                "total_km": item["total_km"],
                "valor_km": item["valor_km"],
                "total_km_valor": item["total_km_valor"],
                "total_geral": item["total_geral"],
            }
            relatorio, created = RelatorioLegado.objects.update_or_create(
                origem=OrigemRelatorioLegado.LEGADO_PLANILHA,
                numero_original_legado=item["numero_original_legado"],
                defaults=defaults,
            )
            relatorio.despesas.all().delete()
            DespesaLegada.objects.bulk_create(
                [
                    DespesaLegada(
                        relatorio=relatorio,
                        ordem=despesa["ordem"],
                        data=despesa["data"],
                        data_original=despesa["data_original"],
                        documento=despesa["documento"],
                        descricao=despesa["descricao"],
                        tipo_codigo=despesa["tipo_codigo"],
                        tipo_descricao=despesa["tipo_descricao"],
                        quantidade=despesa["quantidade"],
                        valor=despesa["valor"],
                        dados_legado_json=despesa["raw"],
                    )
                    for despesa in item["despesas"]
                ]
            )
            resultado.despesas += len(item["despesas"])

            if item["total_km"] or item["total_km_valor"]:
                KmLegado.objects.update_or_create(
                    relatorio=relatorio,
                    defaults={
                        "km": item["total_km"],
                        "valor_km": item["valor_km"],
                        "valor_total": item["total_km_valor"],
                        "dados_legado_json": item["km_raw"],
                    },
                )
                resultado.kms += 1
            else:
                KmLegado.objects.filter(relatorio=relatorio).delete()

            if created:
                resultado.criados += 1
            else:
                resultado.atualizados += 1

    logger.info("Importacao legado finalizada arquivo=%s resultado=%s", path.name, resultado)
    return resultado
