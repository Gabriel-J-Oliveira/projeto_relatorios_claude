import logging
import mimetypes
from pathlib import Path

from django.core.exceptions import ValidationError


ANEXO_EXTENSOES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png"}
ANEXO_MIMES_PERMITIDOS = {"application/pdf", "image/jpeg", "image/png"}
ANEXO_MENSAGEM_FORMATO = "Formato não permitido. Envie apenas PDF, JPG, JPEG ou PNG."
ANEXO_ASSINATURAS = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}

security_logger = logging.getLogger("security")


def obter_tipo_mime_anexo(arquivo):
    nome = getattr(arquivo, "name", "") or ""
    tipo_mime = getattr(arquivo, "content_type", "") or ""
    if not tipo_mime:
        tipo_mime = mimetypes.guess_type(nome)[0] or ""
    return tipo_mime or "application/octet-stream"


def anexo_tem_tipo_permitido(nome_arquivo, tipo_mime=""):
    extensao = Path(nome_arquivo or "").suffix.lower()
    if extensao and extensao not in ANEXO_EXTENSOES_PERMITIDAS:
        return False
    if (
        tipo_mime
        and tipo_mime != "application/octet-stream"
        and tipo_mime not in ANEXO_MIMES_PERMITIDOS
    ):
        return False
    return bool(extensao in ANEXO_EXTENSOES_PERMITIDAS)


def _arquivo_tem_assinatura_permitida(arquivo, extensao):
    assinaturas = ANEXO_ASSINATURAS.get(extensao)
    if not assinaturas:
        return False

    posicao_atual = None
    try:
        if hasattr(arquivo, "tell"):
            posicao_atual = arquivo.tell()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
        cabecalho = arquivo.read(16)
    except Exception:
        return False
    finally:
        if posicao_atual is not None and hasattr(arquivo, "seek"):
            try:
                arquivo.seek(posicao_atual)
            except Exception:
                pass

    return any(cabecalho.startswith(assinatura) for assinatura in assinaturas)


def validar_anexo_upload(arquivo):
    if not arquivo:
        return

    # Arquivos antigos já persistidos não devem bloquear edição de registros legados.
    if getattr(arquivo, "_committed", False) and not getattr(arquivo, "content_type", ""):
        return

    nome = getattr(arquivo, "name", "") or ""
    tipo_mime = obter_tipo_mime_anexo(arquivo)
    tamanho = getattr(arquivo, "size", None)

    if tamanho == 0:
        security_logger.warning("Upload bloqueado: arquivo vazio. nome=%s", nome)
        raise ValidationError("O arquivo enviado está vazio.")

    if not anexo_tem_tipo_permitido(nome, tipo_mime):
        security_logger.warning(
            "Upload bloqueado: tipo nao permitido. nome=%s mime=%s",
            nome,
            tipo_mime,
        )
        raise ValidationError(ANEXO_MENSAGEM_FORMATO)

    extensao = Path(nome or "").suffix.lower()
    if not _arquivo_tem_assinatura_permitida(arquivo, extensao):
        security_logger.warning(
            "Upload bloqueado: assinatura invalida. nome=%s mime=%s",
            nome,
            tipo_mime,
        )
        raise ValidationError(ANEXO_MENSAGEM_FORMATO)
