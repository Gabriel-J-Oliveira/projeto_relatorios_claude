import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from relatorios.models import StatusFinanceiroItem, StatusRelatorio
from relatorios.services.snapshot_service import SnapshotError, validar_snapshot_payload


logger = logging.getLogger(__name__)


EMPRESA_PADRAO = "CONTROLSUL GESTÃO EMPRESARIAL"


class PdfClienteError(Exception):
    pass


@dataclass(frozen=True)
class ItemPdfCliente:
    data: object
    documento: str
    numero_documento: str
    descricao: str
    valor_total: Decimal
    valor: Decimal
    percentual: Decimal | None = None
    km: Decimal | None = None
    valor_km_unitario: Decimal | None = None
    origem_item: str = ""
    origem_id: int | None = None
    comprovante_path: str = ""
    comprovante_nome: str = ""


def _money(valor):
    return Decimal(str(valor or "0.00")).quantize(Decimal("0.01"))


def _percentual(valor, total):
    total = Decimal(str(total or "0.00"))
    if total <= 0:
        return None
    return ((Decimal(str(valor or "0.00")) / total) * Decimal("100")).quantize(
        Decimal("0.01")
    )


def _arquivo_info(arquivo_payload):
    if not arquivo_payload:
        return "", ""
    path = arquivo_payload.get("path") or ""
    nome = arquivo_payload.get("nome") or os.path.basename(path)
    return path, nome


def _data(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _datetime(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_aware(dt):
        return timezone.localtime(dt)
    return dt


def _item_rejeitado(item):
    return bool(
        getattr(item, "rejeitado", False)
        or getattr(item, "status_financeiro", "") == StatusFinanceiroItem.REJEITADO
    )


def _cliente_snapshot(payload, cliente_id):
    cliente_id = int(cliente_id)
    for cliente in payload.get("clientes") or []:
        if int(cliente.get("id") or 0) == cliente_id:
            return cliente
    return None


def _clientes_snapshot(payload):
    return list(payload.get("clientes") or [])


def _relatorio_snapshot_contexto(payload):
    relatorio = payload.get("relatorio") or {}
    assinatura = payload.get("assinatura_temporal") or {}
    return {
        "numero": relatorio.get("numero") or relatorio.get("identificador") or "",
        "identificador": relatorio.get("identificador") or relatorio.get("numero") or "",
        "periodo_inicio": _data(relatorio.get("data_inicio")),
        "periodo_fim": _data(relatorio.get("data_fim")),
        "finalizado_em": _datetime(
            assinatura.get("aprovado_em") or assinatura.get("finalizado_em")
        ),
        "tecnicos": [tecnico.get("nome") for tecnico in payload.get("tecnicos") or [] if tecnico.get("nome")],
        "usa_snapshot": True,
    }


def _itens_snapshot_cliente(payload, cliente_id):
    cliente_id = int(cliente_id)
    itens = []

    for despesa in payload.get("despesas") or []:
        if despesa.get("rejeitado"):
            continue
        rateios = despesa.get("rateios") or []
        if rateios:
            rateios_cliente = [
                rateio
                for rateio in rateios
                if int(rateio.get("cliente_id") or 0) == cliente_id
            ]
        else:
            clientes_despesa = despesa.get("clientes") or payload.get("clientes") or []
            rateios_cliente = (
                [{"valor_final": despesa.get("valor_final")}]
                if len(clientes_despesa) == 1
                and any(int(cliente.get("id") or 0) == cliente_id for cliente in clientes_despesa)
                else []
            )
        total_item = _money(despesa.get("valor_solicitado") or despesa.get("valor_final"))
        comprovante_path, comprovante_nome = _arquivo_info(despesa.get("comprovante"))
        for rateio in rateios_cliente:
            valor = _money(rateio.get("valor_final"))
            if valor <= 0:
                continue
            itens.append(
                ItemPdfCliente(
                    data=_data(despesa.get("data")),
                    documento="Comprovante",
                    numero_documento=despesa.get("numero_documento_comprovante") or "",
                    descricao=despesa.get("descricao") or despesa.get("tipo_label") or "Despesa",
                    valor_total=total_item,
                    valor=valor,
                    percentual=_percentual(valor, total_item),
                    origem_item="despesa",
                    origem_id=despesa.get("id"),
                    comprovante_path=comprovante_path,
                    comprovante_nome=comprovante_nome,
                )
            )

    for trecho in payload.get("trechos_km") or []:
        if trecho.get("rejeitado"):
            continue
        rateios = trecho.get("rateios") or []
        if rateios:
            rateios_cliente = [
                rateio
                for rateio in rateios
                if int(rateio.get("cliente_id") or 0) == cliente_id
            ]
        else:
            clientes_trecho = trecho.get("clientes") or payload.get("clientes") or []
            rateios_cliente = (
                [{"valor_final": trecho.get("valor_final")}]
                if len(clientes_trecho) == 1
                and any(int(cliente.get("id") or 0) == cliente_id for cliente in clientes_trecho)
                else []
            )
        total_item = _money(
            trecho.get("valor_cobranca_calculado")
            or trecho.get("valor_calculado")
            or trecho.get("valor_final")
        )
        for rateio in rateios_cliente:
            valor = _money(rateio.get("valor_cobranca_cliente") or rateio.get("valor_final"))
            if valor <= 0:
                continue
            km_cliente = _money(rateio.get("km_cliente") or rateio.get("km_final") or trecho.get("km"))
            valor_km = _money(
                rateio.get("valor_km_cliente_contratual")
                or rateio.get("valor_km")
            )
            descricao = (
                trecho.get("descricao")
                or f"{trecho.get('origem', '')} -> {trecho.get('destino', '')}".strip(" ->")
                or "Deslocamento"
            )
            itens.append(
                ItemPdfCliente(
                    data=_data(trecho.get("data")),
                    documento="Relatório KM",
                    numero_documento="",
                    descricao=descricao,
                    valor_total=total_item,
                    valor=valor,
                    percentual=_percentual(valor, total_item),
                    km=km_cliente,
                    valor_km_unitario=valor_km,
                    origem_item="trecho",
                    origem_id=trecho.get("id"),
                )
            )

    return sorted(itens, key=lambda item: (item.data is None, item.data, item.documento, item.descricao))


def _clientes_vivos(relatorio):
    return [
        {
            "id": cliente.pk,
            "nome": cliente.nome,
            "documento": cliente.cnpj_cpf or "",
            "cidade_uf": cliente.cidade_uf,
        }
        for cliente in relatorio.clientes_exibicao()
    ]


def _relatorio_vivo_contexto(relatorio):
    logger.warning(
        "Gerando PDF de cliente do relatorio %s com dados vivos por ausencia de snapshot.",
        relatorio.pk,
    )
    return {
        "numero": relatorio.numero or relatorio.identificador,
        "identificador": relatorio.identificador,
        "periodo_inicio": relatorio.data_inicio,
        "periodo_fim": relatorio.data_fim,
        "finalizado_em": relatorio.aprovado_em,
        "tecnicos": [tecnico.nome for tecnico in relatorio.tecnicos_exibicao()],
        "usa_snapshot": False,
    }


def _itens_vivos_cliente(relatorio, cliente_id):
    cliente_id = int(cliente_id)
    itens = []

    for despesa in relatorio.despesas.all():
        if _item_rejeitado(despesa):
            continue
        rateios = list(despesa.rateios.all())
        if rateios:
            valores = [
                (rateio.valor_final, rateio.percentual)
                for rateio in rateios
                if rateio.cliente_id == cliente_id
            ]
        else:
            clientes = list(despesa.clientes_vinculados.all())
            cliente_participa = (
                len(clientes) == 1
                and any(vinculo.cliente_id == cliente_id for vinculo in clientes)
                if clientes
                else relatorio.cliente_id == cliente_id
            )
            valores = [
                (despesa.valor_final, _percentual(despesa.valor_final, despesa.valor_final))
            ] if cliente_participa else []
        comprovante_path = despesa.comprovante.name if despesa.comprovante else ""
        comprovante_nome = os.path.basename(comprovante_path) if comprovante_path else ""
        for valor_rateado, percentual in valores:
            valor = _money(valor_rateado)
            if valor <= 0:
                continue
            itens.append(
                ItemPdfCliente(
                    data=despesa.data,
                    documento="Comprovante",
                    numero_documento=despesa.numero_documento_comprovante or "",
                    descricao=despesa.descricao,
                    valor_total=_money(despesa.valor),
                    valor=valor,
                    percentual=percentual,
                    origem_item="despesa",
                    origem_id=despesa.pk,
                    comprovante_path=comprovante_path,
                    comprovante_nome=comprovante_nome,
                )
            )

    for trecho in relatorio.trechos.all():
        if _item_rejeitado(trecho):
            continue
        rateios = list(trecho.rateios.all())
        if rateios:
            total_trecho = sum((rateio.valor_final for rateio in rateios), Decimal("0.00"))
            valores = [
                (
                    rateio.valor_final,
                    _percentual(rateio.valor_final, total_trecho),
                    rateio.km_cliente,
                    rateio.valor_km,
                )
                for rateio in rateios
                if rateio.cliente_id == cliente_id
            ]
        else:
            clientes = list(trecho.clientes_vinculados.all())
            cliente_participa = (
                len(clientes) == 1
                and any(vinculo.cliente_id == cliente_id for vinculo in clientes)
                if clientes
                else relatorio.cliente_id == cliente_id
            )
            cliente = next((vinculo.cliente for vinculo in clientes if vinculo.cliente_id == cliente_id), None)
            if cliente is None and relatorio.cliente_id == cliente_id:
                cliente = relatorio.cliente
            valor_km_cliente = getattr(cliente, "valor_km", None) if cliente else None
            valor_cliente = _money(trecho.km * valor_km_cliente) if valor_km_cliente else trecho.valor_final_clientes
            valores = [
                (valor_cliente, _percentual(valor_cliente, valor_cliente), trecho.km, valor_km_cliente)
            ] if cliente_participa else []
        for valor_rateado, percentual, km_cliente, valor_km_unitario in valores:
            valor = _money(valor_rateado)
            if valor <= 0:
                continue
            itens.append(
                ItemPdfCliente(
                    data=trecho.data,
                    documento="Relatório KM",
                    numero_documento="",
                    descricao=f"{trecho.origem} -> {trecho.destino}",
                    valor_total=_money(trecho.valor_calculado_clientes),
                    valor=valor,
                    percentual=percentual,
                    km=_money(km_cliente),
                    valor_km_unitario=_money(valor_km_unitario),
                    origem_item="trecho",
                    origem_id=trecho.pk,
                )
            )

    return sorted(itens, key=lambda item: (item.data is None, item.data, item.documento, item.descricao))


def _fonte_dados(relatorio):
    try:
        snapshot = relatorio.snapshot_financeiro
    except ObjectDoesNotExist:
        snapshot = None
    if not snapshot:
        return None
    try:
        validar_snapshot_payload(snapshot.payload or {})
    except SnapshotError as exc:
        logger.error(
            "Snapshot financeiro invalido no PDF de cliente do relatorio %s. Usando fallback legado. Erros: %s",
            relatorio.pk,
            exc,
        )
        return None
    return snapshot.payload or {}


def _logo_empresa_uri():
    logo_path = Path(settings.MEDIA_ROOT) / "Png" / "NovaMarca_cinza (pequeno).png"
    if not logo_path.exists():
        logger.warning("Logo do PDF de cliente nao encontrada em %s.", logo_path)
        return ""
    return logo_path.as_uri()


def listar_clientes_pdf(relatorio):
    payload = _fonte_dados(relatorio)
    if payload:
        return _clientes_snapshot(payload)
    return _clientes_vivos(relatorio)


def montar_contexto_pdf_cliente(relatorio, cliente_id, request=None):
    if relatorio.status != StatusRelatorio.APROVADO:
        raise PermissionDenied("PDF de cliente disponivel apenas para relatorios aprovados.")

    payload = _fonte_dados(relatorio)
    if payload:
        cliente = _cliente_snapshot(payload, cliente_id)
        if not cliente:
            raise PdfClienteError("Cliente nao pertence ao snapshot financeiro do relatorio.")
        contexto_relatorio = _relatorio_snapshot_contexto(payload)
        itens = _itens_snapshot_cliente(payload, cliente_id)
        motivo_viagem = cliente.get("motivo_viagem") or ""
    else:
        clientes = _clientes_vivos(relatorio)
        cliente = next(
            (cliente for cliente in clientes if int(cliente.get("id") or 0) == int(cliente_id)),
            None,
        )
        if not cliente:
            raise PdfClienteError("Cliente nao pertence ao relatorio.")
        contexto_relatorio = _relatorio_vivo_contexto(relatorio)
        itens = _itens_vivos_cliente(relatorio, cliente_id)
        motivo_viagem = ""
        for vinculo in relatorio.clientes_vinculados.all():
            if vinculo.cliente_id == int(cliente_id):
                motivo_viagem = vinculo.motivo_viagem or ""
                break
        if not motivo_viagem and len(clientes) == 1:
            motivo_viagem = relatorio.motivo or ""

    total = sum((item.valor for item in itens), Decimal("0.00")).quantize(Decimal("0.01"))
    if total <= 0:
        raise PdfClienteError("Cliente sem valores aprovados para gerar PDF.")

    emitido_em = timezone.localtime(timezone.now())
    return {
        "empresa": EMPRESA_PADRAO,
        "logo_src": _logo_empresa_uri(),
        "relatorio": contexto_relatorio,
        "cliente": cliente,
        "tecnicos": contexto_relatorio["tecnicos"],
        "motivo_viagem": motivo_viagem,
        "itens": itens,
        "total": total,
        "emitido_em": emitido_em,
        "base_url": request.build_absolute_uri("/") if request else "",
    }


def _render_pdf_cliente(contexto):
    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        raise PdfClienteError(
            "WeasyPrint nao esta disponivel neste ambiente."
        ) from exc

    html = render_to_string("relatorios/pdf/cliente.html", contexto)
    css_path = settings.BASE_DIR / "static" / "css" / "pdf_cliente.css"
    return HTML(
        string=html,
        encoding="utf-8",
        base_url=contexto.get("base_url") or "",
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])


def nome_arquivo_pdf_cliente(relatorio, cliente):
    numero = slugify(str(relatorio.numero or relatorio.identificador)) or str(relatorio.pk)
    nome_cliente = slugify(cliente.get("nome") or "cliente") or "cliente"
    return f"relatorio_{numero}_{nome_cliente}.pdf"


def _pasta_cliente(cliente):
    return slugify(cliente.get("nome") or "cliente") or "cliente"


def _arquivo_anexo_path(nome_storage):
    if not nome_storage:
        return None
    raiz = Path(getattr(settings, "ANEXOS_ROOT", settings.MEDIA_ROOT)).resolve()
    caminho = (raiz / nome_storage).resolve()
    try:
        caminho.relative_to(raiz)
    except ValueError:
        logger.warning("Anexo fora de ANEXOS_ROOT ignorado no ZIP: %s", nome_storage)
        return None
    return caminho if caminho.exists() and caminho.is_file() else None


def _adicionar_anexos_cliente(arquivo_zip, pasta_cliente, itens):
    usados = set()
    for item in itens:
        caminho = _arquivo_anexo_path(item.comprovante_path)
        if not caminho:
            continue
        nome_base = item.comprovante_nome or caminho.name
        prefixo = item.origem_item or "item"
        item_id = item.origem_id or "sem-id"
        nome_zip = f"{pasta_cliente}/comprovantes/{prefixo}_{item_id}_{nome_base}"
        contador = 2
        while nome_zip in usados:
            stem = Path(nome_base).stem
            suffix = Path(nome_base).suffix
            nome_zip = f"{pasta_cliente}/comprovantes/{prefixo}_{item_id}_{stem}_{contador}{suffix}"
            contador += 1
        arquivo_zip.write(caminho, nome_zip)
        usados.add(nome_zip)


def gerar_pdf_cliente(relatorio, cliente_id, request=None):
    contexto = montar_contexto_pdf_cliente(relatorio, cliente_id, request=request)
    return _render_pdf_cliente(contexto), contexto


def gerar_zip_pdfs_clientes(relatorio, request=None):
    clientes = listar_clientes_pdf(relatorio)
    buffer = BytesIO()
    gerados = []
    ignorados = []

    with ZipFile(buffer, "w", ZIP_DEFLATED) as arquivo_zip:
        for cliente in clientes:
            cliente_id = cliente.get("id")
            try:
                pdf, contexto = gerar_pdf_cliente(relatorio, cliente_id, request=request)
            except PdfClienteError as exc:
                logger.info(
                    "PDF de cliente ignorado no relatorio %s para cliente %s: %s",
                    relatorio.pk,
                    cliente_id,
                    exc,
                )
                ignorados.append((cliente, str(exc)))
                continue
            filename = nome_arquivo_pdf_cliente(relatorio, contexto["cliente"])
            pasta = _pasta_cliente(contexto["cliente"])
            caminho_pdf = f"{pasta}/{filename}"
            arquivo_zip.writestr(caminho_pdf, pdf)
            _adicionar_anexos_cliente(arquivo_zip, pasta, contexto.get("itens") or [])
            gerados.append(caminho_pdf)

    if not gerados:
        raise PdfClienteError("Nenhum cliente possui valores aprovados para gerar PDF.")

    buffer.seek(0)
    return buffer.getvalue(), gerados, ignorados
