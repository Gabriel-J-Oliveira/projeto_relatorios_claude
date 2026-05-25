import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


ANEXO_EXTENSOES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png"}
ANEXO_MIMES_PERMITIDOS = {"application/pdf", "image/jpeg", "image/png"}
ANEXO_MENSAGEM_FORMATO = "Formato não permitido. Envie apenas PDF, JPG, JPEG ou PNG."


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
    if tipo_mime and tipo_mime != "application/octet-stream" and tipo_mime not in ANEXO_MIMES_PERMITIDOS:
        return False
    return bool(extensao in ANEXO_EXTENSOES_PERMITIDAS)


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
        raise ValidationError("O arquivo enviado está vazio.")

    limite_mb = getattr(settings, "ANEXO_MAX_UPLOAD_MB", 10)
    limite_bytes = int(limite_mb) * 1024 * 1024
    if tamanho and tamanho > limite_bytes:
        raise ValidationError(f"O arquivo excede o limite de {limite_mb} MB.")

    if not anexo_tem_tipo_permitido(nome, tipo_mime):
        raise ValidationError(ANEXO_MENSAGEM_FORMATO)
