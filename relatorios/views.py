import logging
import json
import mimetypes
import re
import time
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import get_valid_filename
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView, TemplateView

from .models import (
    Adiantamento,
    AnexoRelatorio,
    ArtigoAjuda,
    CategoriaAjuda,
    EmailLog,
    ImagemAjuda,
    Cliente,
    ItemDespesa,
    Municipio,
    PerfilUsuario,
    PoliticaValor,
    RelatorioLegado,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusEmailLog,
    StatusRelatorio,
    Tecnico,
    TipoDespesa,
    TipoEventoHistorico,
    TrechoKm,
    valor_km_control_sul,
    normalizar_nome_pessoa,
)
from .services.historico_service import registrar_evento
from .services.email_service import EmailNotificacaoError, enviar_report_suporte
from .services.clientes_relatorio_service import (
    normalizar_ids_clientes,
    obter_clientes_relatorio,
    obter_motivos_clientes_relatorio,
    sync_clientes_despesa,
    sync_clientes_relatorio,
    sync_clientes_trecho,
)
from .services.clientes_valor_km_service import (
    clientes_pendentes_valor_km,
    clientes_relatorio_sem_valor_km,
    salvar_valor_km_cliente,
    salvar_valores_km_clientes,
)
from .validators import anexo_tem_tipo_permitido
from .services.autorizacao_service import (
    exigir_acesso_erp,
    exigir_administrativo,
    exigir_financeiro,
    queryset_relatorios_visiveis,
    usuario_eh_administrativo,
    usuario_pode_acessar_erp,
    usuario_pode_atuar_como_financeiro,
    usuario_pode_editar_relatorio,
    usuario_pode_enviar_relatorio,
    usuario_pode_visualizar_relatorio,
    usuario_eh_superadmin,
    usuario_pode_acessar_manutencao,
)
from .services.workflow_service import (
    WorkflowError,
    aprovar_relatorio,
    enviar_para_conferencia,
    preparar_rascunho_para_salvar,
    rejeitar_relatorio,
    relatorio_bloqueado as workflow_relatorio_bloqueado,
    solicitar_ajuste,
    _salvar_valores_aprovados,
)
from .services.manutencao_service import (
    buscar_logs,
    enviar_email_teste,
    filtrar_emails,
    reenviar_emails,
    reenviar_email_log,
    resumo_emails,
)
from .services.rateio_service import (
    RateioError,
    garantir_rateio_despesa,
    garantir_rateio_trecho,
    garantir_rateios_relatorio,
    salvar_rateio_despesa,
    salvar_rateio_trecho,
    serializar_rateio,
)
from .services.resumo_cliente_service import resumo_financeiro_por_cliente
from .services.consulta_relatorio_service import montar_consulta_relatorio
from .services.financeiro_validator import validar_integridade_financeira_relatorio
from .services.financeiro_detail_service import montar_payload_financeiro_por_id
from .services.pdf_cliente_service import (
    PdfClienteError,
    gerar_pdf_cliente,
    gerar_zip_pdfs_clientes,
    nome_arquivo_pdf_cliente,
)
from .services.pdf_interno_service import montar_contexto_pdf_interno
from .services.maps_service import MapsServiceError, buscar_endereco, calcular_rota
from .services.dashboard_service import get_dashboard_context, get_dashboard_data
from .services.help_center_service import (
    contexto_central_ajuda,
    materializar_artigo_arquivo,
    obter_artigo,
    obter_categoria,
    sanitizar_html,
    usuario_pode_editar_ajuda,
)
from .forms import (
    AdiantamentoForm,
    ArtigoAjudaForm,
    ClienteForm,
    CompletarCadastroUsuarioForm,
    ItemDespesaForm,
    ItemDespesaFormSet,
    RelatorioFiltroForm,
    RelatorioTecnicoForm,
    TecnicoForm,
    TrechoKmForm,
    TrechoKmFormSet,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")
help_logger = logging.getLogger("relatorios.help_center")
rascunhos_logger = logging.getLogger("relatorios.rascunhos")

BLOQUEIO_POS_APROVACAO = {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


def _client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _registrar_bloqueio_seguranca(request, mensagem, **extra):
    security_logger.warning(
        "%s usuario=%s username=%s ip=%s metodo=%s path=%s extra=%s",
        mensagem,
        getattr(request.user, "pk", None),
        getattr(request.user, "username", ""),
        _client_ip(request),
        request.method,
        request.path,
        extra,
    )


def _log_envio_relatorio_pre_autorizacao(request, relatorio, origem):
    logger.info(
        "ENVIO_RELATORIO_PRE_AUTH origem=%s user_id=%s username=%s relatorio_id=%s "
        "status=%s criado_por_id=%s tecnico_responsavel_id=%s tecnico_reembolso_id=%s",
        origem,
        getattr(request.user, "pk", None),
        getattr(request.user, "username", ""),
        getattr(relatorio, "pk", None),
        getattr(relatorio, "status", None),
        getattr(relatorio, "criado_por_id", None),
        getattr(relatorio, "tecnico_responsavel_id", None),
        getattr(relatorio, "tecnico_reembolso_id", None),
    )


@login_required
@exigir_acesso_erp
def ajuda_index_view(request):
    termo = (request.GET.get("q") or "").strip()
    return render(
        request,
        "ajuda/index.html",
        contexto_central_ajuda(request.user, termo),
    )


@login_required
@exigir_acesso_erp
def ajuda_categoria_view(request, slug):
    termo = (request.GET.get("q") or "").strip()
    contexto = obter_categoria(request.user, slug, termo)
    if not contexto:
        raise Http404("Categoria de ajuda não encontrada.")
    return render(request, "ajuda/categoria.html", contexto)


@login_required
@exigir_acesso_erp
def ajuda_artigo_view(request, slug):
    artigo = obter_artigo(request.user, slug)
    if not artigo:
        raise Http404("Artigo de ajuda não encontrado.")
    return render(request, "ajuda/artigo.html", artigo)


@login_required
@exigir_acesso_erp
def ajuda_artigo_editar_view(request, slug):
    if not usuario_pode_editar_ajuda(request.user):
        raise PermissionDenied("Usuário sem permissão para editar a Central de Ajuda.")

    artigo = materializar_artigo_arquivo(slug, request.user)
    if not artigo or not artigo.ativo:
        raise Http404("Artigo de ajuda não encontrado.")

    if request.method == "POST":
        form = ArtigoAjudaForm(request.POST, instance=artigo)
        if form.is_valid():
            artigo = form.save(commit=False)
            artigo.conteudo = sanitizar_html(artigo.conteudo)
            artigo.atualizado_por = request.user
            artigo.save()
            form.save_m2m()
            messages.success(request, "Artigo atualizado com sucesso.")
            return redirect("relatorios:ajuda_artigo", slug=artigo.slug)
    else:
        form = ArtigoAjudaForm(instance=artigo)

    return render(
        request,
        "ajuda/editar_artigo.html",
        {"form": form, "article": artigo, "can_edit_help": True},
    )


@login_required
@exigir_acesso_erp
def ajuda_artigo_criar_view(request, slug):
    if not usuario_pode_editar_ajuda(request.user):
        raise PermissionDenied("Usuário sem permissão para criar artigos na Central de Ajuda.")

    categoria = get_object_or_404(CategoriaAjuda, slug=slug, ativo=True)
    artigo = ArtigoAjuda(categoria=categoria, conteudo="")

    if request.method == "POST":
        form = ArtigoAjudaForm(request.POST, instance=artigo)
        if form.is_valid():
            artigo = form.save(commit=False)
            artigo.conteudo = sanitizar_html(artigo.conteudo)
            artigo.criado_por = request.user
            artigo.atualizado_por = request.user
            artigo.save()
            form.save_m2m()
            messages.success(request, "Artigo criado com sucesso.")
            return redirect("relatorios:ajuda_artigo", slug=artigo.slug)
    else:
        form = ArtigoAjudaForm(instance=artigo, initial={"categoria": categoria})

    return render(
        request,
        "ajuda/editar_artigo.html",
        {
            "form": form,
            "article": artigo,
            "category": categoria,
            "can_edit_help": True,
            "is_create": True,
        },
    )


@login_required
@exigir_acesso_erp
@require_POST
def ajuda_artigo_excluir_view(request, slug):
    if not usuario_pode_editar_ajuda(request.user):
        help_logger.warning(
            "Tentativa sem permissao de excluir artigo da ajuda. usuario=%s slug=%s",
            request.user.pk,
            slug,
        )
        raise PermissionDenied("Usuário sem permissão para excluir artigos da Central de Ajuda.")

    artigo = materializar_artigo_arquivo(slug, request.user)
    if not artigo:
        raise Http404("Artigo de ajuda não encontrado.")
    titulo = artigo.titulo
    categoria_slug = artigo.categoria.slug if artigo.categoria_id else ""
    artigo.artigos_relacionados.clear()
    artigo.relacionado_em.clear()
    artigo.ativo = False
    artigo.atualizado_por = request.user
    artigo.save(update_fields=["ativo", "atualizado_por", "atualizado_em"])
    help_logger.info(
        "Artigo da ajuda excluido. usuario=%s artigo_slug=%s artigo_titulo=%s",
        request.user.pk,
        slug,
        titulo,
    )
    messages.success(request, f"Artigo \"{titulo}\" excluído definitivamente.")
    if categoria_slug:
        return redirect("relatorios:ajuda_categoria", slug=categoria_slug)
    return redirect("relatorios:ajuda_index")


HELP_IMAGE_EXTENSOES = {".png", ".jpg", ".jpeg", ".webp"}
HELP_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
HELP_IMAGE_ASSINATURAS = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}


def _validar_help_image(arquivo):
    nome = getattr(arquivo, "name", "") or ""
    ext = "." + nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    tipo_mime = getattr(arquivo, "content_type", "") or mimetypes.guess_type(nome)[0] or ""
    tamanho = getattr(arquivo, "size", 0) or 0
    if not arquivo or tamanho <= 0:
        raise ValidationError("O arquivo enviado está vazio.")
    if ext not in HELP_IMAGE_EXTENSOES or tipo_mime not in HELP_IMAGE_MIMES:
        raise ValidationError("Formato não permitido. Envie apenas PNG, JPG, JPEG ou WEBP.")
    limite_mb = int(getattr(settings, "HELP_IMAGE_MAX_UPLOAD_MB", 5))
    if tamanho > limite_mb * 1024 * 1024:
        raise ValidationError(f"A imagem excede o limite de {limite_mb} MB.")
    pos = arquivo.tell() if hasattr(arquivo, "tell") else None
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)
    cabecalho = arquivo.read(16)
    if pos is not None and hasattr(arquivo, "seek"):
        arquivo.seek(pos)
    assinaturas = HELP_IMAGE_ASSINATURAS.get(ext) or ()
    if not any(cabecalho.startswith(assinatura) for assinatura in assinaturas):
        raise ValidationError("Formato não permitido. Envie apenas PNG, JPG, JPEG ou WEBP.")
    if ext == ".webp" and b"WEBP" not in cabecalho:
        raise ValidationError("Formato não permitido. Envie apenas PNG, JPG, JPEG ou WEBP.")
    return tipo_mime


@login_required
@require_POST
@exigir_acesso_erp
def ajuda_imagem_upload_view(request):
    if not usuario_pode_editar_ajuda(request.user):
        help_logger.warning(
            "Tentativa sem permissao de upload de imagem da ajuda. usuario=%s path=%s",
            getattr(request.user, "pk", None),
            request.path,
        )
        return JsonResponse(
            {"success": False, "error": "Você não tem permissão para enviar imagens."},
            status=403,
        )
    arquivo = request.FILES.get("file") or request.FILES.get("imagem")
    try:
        tipo_mime = _validar_help_image(arquivo)
    except ValidationError as exc:
        help_logger.warning(
            "Upload de imagem da ajuda bloqueado. usuario=%s nome=%s erro=%s",
            getattr(request.user, "pk", None),
            getattr(arquivo, "name", ""),
            "; ".join(exc.messages),
        )
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=400)

    nome_original = get_valid_filename(getattr(arquivo, "name", "") or "imagem")
    imagem = ImagemAjuda.objects.create(
        arquivo=arquivo,
        nome_original=nome_original,
        tipo_mime=tipo_mime,
        tamanho_bytes=getattr(arquivo, "size", 0) or 0,
        enviado_por=request.user,
    )
    url = reverse("relatorios:ajuda_imagem_visualizar", args=[imagem.pk])
    help_logger.info(
        "Imagem da ajuda enviada. usuario=%s imagem=%s nome=%s tamanho=%s mime=%s",
        getattr(request.user, "pk", None),
        imagem.pk,
        imagem.nome_original,
        imagem.tamanho_bytes,
        imagem.tipo_mime,
    )
    return JsonResponse(
        {
            "success": True,
            "id": imagem.pk,
            "location": url,
            "url": url,
            "message": "Imagem enviada com sucesso.",
        }
    )


@login_required
@exigir_acesso_erp
def ajuda_imagem_visualizar_view(request, pk):
    imagem = get_object_or_404(ImagemAjuda, pk=pk)
    try:
        arquivo = imagem.arquivo.open("rb")
    except FileNotFoundError:
        raise Http404("Imagem não encontrada.")
    response = FileResponse(arquivo, content_type=imagem.tipo_mime or "application/octet-stream")
    response["Content-Disposition"] = content_disposition_header(
        False,
        imagem.nome_original or imagem.arquivo.name.rsplit("/", 1)[-1],
    )
    return response


@login_required
@require_POST
def marcar_tour_guiado_visto(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = request.POST

    chave = str(payload.get("tour") or "").strip()
    chaves_permitidas = {
        "dashboardTourVisto:v1",
        "relatorioFormTourVisto:v1",
        "relatoriosListTourVisto:v1",
        "relatorioDetailTourVisto:v1",
    }
    if chave not in chaves_permitidas:
        return JsonResponse({"success": False, "error": "Tour invalido."}, status=400)

    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=request.user)
    vistos = dict(perfil.tours_guiados_vistos or {})
    vistos[chave] = timezone.now().isoformat()
    perfil.tours_guiados_vistos = vistos
    perfil.save(update_fields=["tours_guiados_vistos", "atualizado_em"])
    return JsonResponse({"success": True})


@login_required
@require_POST
def suporte_reportar_view(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = request.POST

    tipo = str(payload.get("tipo") or "").strip().lower()
    assunto = " ".join(str(payload.get("assunto") or "").replace("\r", " ").replace("\n", " ").split())
    descricao = str(payload.get("descricao") or "").strip()
    pagina_atual = str(payload.get("pagina_atual") or "").strip()[:1000]

    if tipo not in {"problema", "sugestao"}:
        logger.warning("Report suporte invalido: tipo=%s usuario=%s", tipo, request.user.pk)
        return JsonResponse({"success": False, "error": "Selecione um tipo válido."}, status=400)
    if not assunto or len(assunto) > 150:
        return JsonResponse({"success": False, "error": "Informe um assunto com até 150 caracteres."}, status=400)
    if len(descricao) < 10 or len(descricao) > 3000:
        return JsonResponse({"success": False, "error": "Informe uma descrição entre 10 e 3000 caracteres."}, status=400)

    try:
        enviar_report_suporte(
            request.user,
            tipo,
            assunto,
            descricao,
            pagina_atual=pagina_atual,
            request=request,
        )
    except EmailNotificacaoError:
        logger.exception(
            "Falha ao enviar report de suporte usuario=%s tipo=%s assunto=%s",
            request.user.pk,
            tipo,
            assunto,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "Não foi possível enviar a mensagem. Tente novamente ou contate o suporte.",
            },
            status=502,
        )

    return JsonResponse({"success": True, "message": "Mensagem enviada com sucesso."})


@login_required
def perfil_usuario_view(request):
    perfil, _criado = PerfilUsuario.objects.select_related("setor").get_or_create(usuario=request.user)
    tipo_usuario = "Administrador" if usuario_eh_administrativo(request.user) else "Usuário"
    return render(
        request,
        "registration/perfil_usuario.html",
        {
            "perfil": perfil,
            "tipo_usuario": tipo_usuario,
            "titulo_pagina": "Meu perfil",
        },
    )


def _exigir_manutencao(request):
    if not usuario_pode_acessar_manutencao(request.user):
        logger.warning(
            "manutencao_acesso_negado usuario=%s path=%s",
            getattr(request.user, "pk", None),
            request.path,
        )
        raise PermissionDenied("Você não tem permissão para acessar a manutenção do sistema.")


@login_required
def manutencao_view(request):
    _exigir_manutencao(request)
    logs = buscar_logs(
        data_inicio=request.GET.get("log_data_inicio"),
        data_fim=request.GET.get("log_data_fim"),
        nivel=request.GET.get("log_nivel"),
        termo=request.GET.get("log_q"),
        logger_nome=request.GET.get("log_logger"),
        limite=request.GET.get("log_limite") or 200,
    )
    emails = list(filtrar_emails(request.GET)[:100])
    logger.info(
        "manutencao_acesso usuario=%s linhas_log=%s emails=%s",
        request.user.pk,
        len(logs.get("linhas", [])),
        len(emails),
    )
    return render(
        request,
        "manutencao/index.html",
        {
            "titulo_pagina": "Manutenção do Sistema",
            "logs": logs,
            "email_logs": emails,
            "email_status_choices": StatusEmailLog.choices,
            "email_resumo": resumo_emails(),
            "aba_ativa": request.GET.get("aba") or "logs",
            "log_niveis": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        },
    )


@require_POST
@login_required
def manutencao_email_reenviar_view(request, pk):
    _exigir_manutencao(request)
    email_log = get_object_or_404(EmailLog, pk=pk)
    if email_log.status not in {StatusEmailLog.PENDENTE, StatusEmailLog.FALHA}:
        messages.warning(request, "Somente e-mails pendentes ou com falha podem ser reenviados por esta ação.")
        return redirect(f"{reverse('relatorios:manutencao')}?aba=emails")
    try:
        reenviar_email_log(email_log, usuario=request.user)
    except Exception as exc:
        messages.error(request, "Falha ao reenviar e-mail. Verifique o detalhe do erro.")
        logger.error(
            "manutencao_email_reenvio_falha usuario=%s email_log=%s erro=%s",
            request.user.pk,
            pk,
            exc,
        )
    else:
        messages.success(request, "E-mail reenviado com sucesso.")
    return redirect(f"{reverse('relatorios:manutencao')}?aba=emails")


@require_POST
@login_required
def manutencao_emails_reenviar_lote_view(request):
    _exigir_manutencao(request)
    resultado = reenviar_emails(request.POST.getlist("email_ids"), usuario=request.user, limite=20)
    if resultado.enviados:
        messages.success(request, f"{resultado.enviados} e-mail(s) reenviado(s) com sucesso.")
    if resultado.falhas:
        messages.error(request, f"{resultado.falhas} e-mail(s) falharam no reenvio.")
    if resultado.ignorados:
        messages.warning(request, f"{resultado.ignorados} e-mail(s) foram ignorados.")
    for mensagem in (resultado.mensagens or [])[:5]:
        messages.warning(request, mensagem)
    logger.info(
        "manutencao_email_reenvio_lote usuario=%s enviados=%s falhas=%s ignorados=%s",
        request.user.pk,
        resultado.enviados,
        resultado.falhas,
        resultado.ignorados,
    )
    return redirect(f"{reverse('relatorios:manutencao')}?aba=emails")


@require_POST
@login_required
def manutencao_email_teste_view(request):
    _exigir_manutencao(request)
    destinatario = request.POST.get("destinatario_teste")
    try:
        enviar_email_teste(destinatario, usuario=request.user)
    except Exception as exc:
        messages.error(request, f"Falha ao enviar e-mail de teste: {exc}")
        logger.error(
            "manutencao_email_teste_falha usuario=%s destinatario=%s erro=%s",
            request.user.pk,
            destinatario,
            exc,
        )
    else:
        messages.success(request, "E-mail de teste enviado com sucesso.")
    return redirect(f"{reverse('relatorios:manutencao')}?aba=emails")


@login_required
@require_POST
def clientes_valor_km_salvar_view(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = {}

    itens = payload.get("clientes") or []
    if not isinstance(itens, list):
        return JsonResponse({"success": False, "error": "Payload invalido."}, status=400)

    try:
        atualizados = salvar_valores_km_clientes(itens, request.user)
    except PermissionDenied:
        logger.warning("Tentativa sem permissao de salvar valor_km usuario=%s", request.user.pk)
        return JsonResponse(
            {"success": False, "error": "Voce nao tem permissao para alterar valor de KM."},
            status=403,
        )
    except ValidationError as exc:
        mensagens = exc.messages if hasattr(exc, "messages") else [str(exc)]
        return JsonResponse({"success": False, "error": " ".join(mensagens)}, status=400)
    except Exception as exc:
        logger.exception("Erro ao salvar valores KM de clientes: %s", exc)
        return JsonResponse(
            {"success": False, "error": "Nao foi possivel salvar os valores. Tente novamente."},
            status=500,
        )

    return JsonResponse(
        {
            "success": True,
            "message": f"{atualizados} valor(es) de KM atualizado(s) com sucesso.",
            "atualizados": atualizados,
        }
    )


@login_required
@require_POST
def cliente_valor_km_salvar_view(request, pk):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = request.POST

    cliente = get_object_or_404(Cliente, pk=pk, ativo=True)
    try:
        salvar_valor_km_cliente(
            cliente,
            payload.get("valor_km"),
            request.user,
            payload.get("observacao", "Listagem de clientes"),
        )
    except PermissionDenied:
        logger.warning("Tentativa sem permissao de salvar valor_km usuario=%s cliente=%s", request.user.pk, pk)
        return JsonResponse(
            {"success": False, "error": "Voce nao tem permissao para alterar valor de KM."},
            status=403,
        )
    except ValidationError as exc:
        mensagens = exc.messages if hasattr(exc, "messages") else [str(exc)]
        return JsonResponse({"success": False, "error": " ".join(mensagens)}, status=400)
    except Exception as exc:
        logger.exception("Erro ao salvar valor KM do cliente %s: %s", pk, exc)
        return JsonResponse(
            {"success": False, "error": "Nao foi possivel salvar o valor. Tente novamente."},
            status=500,
        )

    pendentes = clientes_pendentes_valor_km(request.user).count()
    return JsonResponse(
        {
            "success": True,
            "message": "Valor de KM salvo com sucesso.",
            "valor_km": str(cliente.valor_km),
            "pendentes": pendentes,
        }
    )


@login_required
def completar_cadastro_view(request):
    perfil, _criado = PerfilUsuario.objects.get_or_create(usuario=request.user)
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = CompletarCadastroUsuarioForm(request.POST, user=request.user, perfil=perfil)
        if form.is_valid():
            request.user.first_name = form.cleaned_data["first_name"].strip()
            request.user.last_name = form.cleaned_data["last_name"].strip()
            request.user.email = form.cleaned_data["email"]
            request.user.save(update_fields=["first_name", "last_name", "email"])
            perfil.refresh_from_db()
            perfil.cadastro_confirmado_em = timezone.now()
            perfil.save(update_fields=["cadastro_confirmado_em", "atualizado_em"])
            messages.success(request, "Dados cadastrais confirmados com sucesso.")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("relatorios:dashboard")
    else:
        form = CompletarCadastroUsuarioForm(user=request.user, perfil=perfil)

    return render(
        request,
        "registration/completar_cadastro.html",
        {
            "form": form,
            "next": next_url,
            "perfil": perfil,
        },
    )


# ─────────────────────────────────────────────
# HELPERS INTERNOS
# ─────────────────────────────────────────────


def _get_valor_km_para_cliente(cliente_id) -> float:
    """
    Busca o valor_km real do model Cliente.
    Retorna float 0.0 se não encontrado ou inválido.

    IMPORTANTE: o campo real no model é `valor_km`.
    O nome `valor_km_padrao` é APENAS uma variável auxiliar
    usada no frontend e nos kwargs dos forms — nunca no banco.
    """
    if not cliente_id:
        return 0.0
    try:
        valor = (
            Cliente.objects.filter(pk=cliente_id)
            .values_list("valor_km", flat=True)  # campo real do model
            .first()
        )
        return float(valor) if valor else 0.0
    except (TypeError, ValueError):
        logger.warning(
            "_get_valor_km_para_cliente: valor inválido para cliente_id=%s", cliente_id
        )
        return 0.0


def _form_has_content(form) -> bool:
    """Retorna True se o form tem dados reais (não está vazio e não está marcado para DELETE)."""
    if not hasattr(form, "cleaned_data"):
        return False
    if form.cleaned_data.get("DELETE"):
        return False
    values = [
        v
        for k, v in form.cleaned_data.items()
        if k not in {"DELETE", "id", "relatorio", "ordem", "quem_pagou"}
    ]
    return any(v not in (None, "", [], ()) for v in values)


def _clientes_queryset_selecao():
    return Cliente.objects.filter(ativo=True).order_by(
        "nome_fantasia",
        "razao_social",
        "nome",
    )


def _nome_cliente(cliente):
    if not cliente:
        return "Nao informado"
    return getattr(cliente, "nome_exibicao", None) or cliente.nome


def _clientes_selecionados_do_request(request, instance=None):
    if request.method == "POST":
        ids = normalizar_ids_clientes(request.POST.get("clientes_relatorio"))
        clientes_por_id = {
            cliente.pk: _nome_cliente(cliente)
            for cliente in Cliente.objects.filter(pk__in=ids)
        }
        nomes = [clientes_por_id[cliente_id] for cliente_id in ids if cliente_id in clientes_por_id]
        return ids, nomes

    if instance:
        clientes = list(obter_clientes_relatorio(instance))
        return [cliente.pk for cliente in clientes], [_nome_cliente(cliente) for cliente in clientes]

    return [], []


def _motivos_clientes_do_request(request, instance=None):
    motivos = {}
    if request.method == "POST":
        for cliente_id in normalizar_ids_clientes(request.POST.get("clientes_relatorio")):
            motivos[cliente_id] = (
                request.POST.get(f"motivo_cliente_{cliente_id}") or ""
            ).strip()
        return motivos
    return obter_motivos_clientes_relatorio(instance) if instance else {}


def _tecnicos_selecionados_do_request(request, instance=None):
    if request.method == "POST":
        ids = []
        responsavel_id = request.POST.get("tecnico_responsavel")
        if responsavel_id:
            ids.append(responsavel_id)
        ids.extend(request.POST.getlist("tecnicos_equipe"))
        ids = normalizar_ids_clientes(ids)
        tecnicos = {
            tecnico.pk: tecnico.nome
            for tecnico in Tecnico.objects.filter(pk__in=ids)
        }
        nomes = [tecnicos[pk] for pk in ids if pk in tecnicos]
        return ids, nomes

    if instance:
        tecnicos = []
        if instance.tecnico_responsavel_id:
            tecnicos.append(instance.tecnico_responsavel)
        tecnicos.extend(instance.tecnicos_adicionais.order_by("nome"))
        return [tecnico.pk for tecnico in tecnicos], [tecnico.nome for tecnico in tecnicos]

    return [], []


def _clientes_item_post(request, prefix):
    return normalizar_ids_clientes(request.POST.get(f"{prefix}-clientes"))


def _clientes_item_instance_value(instance):
    if not getattr(instance, "pk", None):
        return ""
    try:
        ids = instance.clientes_vinculados.values_list("cliente_id", flat=True)
    except Exception:
        return ""
    return ",".join(str(cliente_id) for cliente_id in ids)


def _popular_clientes_formset_para_template(formset, request):
    for form in getattr(formset, "forms", []):
        chave = f"{form.prefix}-clientes"
        if request.method == "POST":
            valor = request.POST.get(chave, "")
        else:
            valor = _clientes_item_instance_value(form.instance)
        form.clientes_value = valor
        form.clientes_value_set = True


def _popular_clientes_formsets_para_template(request, fs_desp, fs_km):
    _popular_clientes_formset_para_template(fs_desp, request)
    _popular_clientes_formset_para_template(fs_km, request)


def _registrar_metadados_comprovante(relatorio, usuario, item, arquivo_original=None):
    arquivo = getattr(item, "comprovante", None)
    if not arquivo:
        return
    if isinstance(item, ItemDespesa):
        AnexoRelatorio.registrar_comprovante(
            relatorio=relatorio,
            usuario=usuario,
            despesa=item,
            arquivo=arquivo,
            arquivo_original=arquivo_original,
        )


def _nome_arquivo_anexo(arquivo):
    return (getattr(arquivo, "name", "") or "anexo").rsplit("/", 1)[-1]


def _tipo_mime_arquivo(nome_arquivo, tipo_mime=""):
    return tipo_mime or mimetypes.guess_type(nome_arquivo or "")[0] or "application/octet-stream"


def _responder_arquivo_anexo(arquivo, *, nome_original="", tipo_mime="", download=False):
    if not arquivo:
        raise Http404("Arquivo não encontrado.")
    nome = nome_original or _nome_arquivo_anexo(arquivo)
    content_type = _tipo_mime_arquivo(nome, tipo_mime)
    if not anexo_tem_tipo_permitido(nome, content_type):
        content_type = "application/octet-stream"
        download = True
    try:
        arquivo.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Arquivo não encontrado.") from exc
    response = FileResponse(arquivo.file, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=download,
        filename=nome,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _validar_acesso_relatorio_arquivo(user, relatorio, request=None):
    if not usuario_pode_visualizar_relatorio(user, relatorio):
        if request is not None:
            _registrar_bloqueio_seguranca(
                request,
                "Acesso a arquivo bloqueado",
                relatorio_id=getattr(relatorio, "pk", None),
            )
        raise PermissionDenied("Você não tem permissão para acessar este anexo.")


def _anexos_visualizacao_relatorio(relatorio):
    anexos = []
    for despesa in relatorio.despesas.all():
        if not despesa.comprovante:
            continue
        nome = _nome_arquivo_anexo(despesa.comprovante)
        tipo_mime = _tipo_mime_arquivo(nome)
        anexos.append(
            SimpleNamespace(
                tipo="Comprovante",
                descricao=despesa.descricao,
                nome=nome,
                tipo_mime=tipo_mime,
                preview_url=reverse("relatorios:despesa_comprovante_preview", kwargs={"pk": despesa.pk}),
                download_url=reverse("relatorios:despesa_comprovante_baixar", kwargs={"pk": despesa.pk}),
            )
        )
    for anexo in relatorio.anexos.filter(despesa__isnull=True, trecho__isnull=True):
        nome = anexo.nome_original or _nome_arquivo_anexo(anexo.arquivo)
        tipo_mime = _tipo_mime_arquivo(nome, anexo.tipo_mime)
        anexos.append(
            SimpleNamespace(
                tipo="Anexo",
                descricao=anexo.observacao or anexo.nome_original,
                nome=nome,
                tipo_mime=tipo_mime,
                preview_url=reverse("relatorios:anexo_preview", kwargs={"pk": anexo.pk}),
                download_url=reverse("relatorios:anexo_baixar", kwargs={"pk": anexo.pk}),
            )
        )
    return anexos


def _validar_clientes_formsets(request, fs_desp, fs_km, cliente_ids_relatorio):
    erros = []
    clientes_relatorio = set(cliente_ids_relatorio)

    if not cliente_ids_relatorio:
        erros.append("Selecione ao menos um cliente para o relatório.")

    def linha_tem_conteudo(form):
        return _form_has_content(form)

    for form in fs_desp.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        if not linha_tem_conteudo(form):
            continue
        ids = _clientes_item_post(request, form.prefix)
        if not ids and len(clientes_relatorio) == 1:
            continue
        if not ids:
            erros.append("Toda despesa deve informar os clientes envolvidos.")
        elif set(ids) - clientes_relatorio:
            erros.append("Despesa referencia cliente fora do relatório.")

    for form in fs_km.forms:
        if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
            continue
        if not linha_tem_conteudo(form):
            continue
        ids = _clientes_item_post(request, form.prefix)
        if not ids and len(clientes_relatorio) == 1:
            continue
        if not ids:
            erros.append("Todo trecho de KM deve informar os clientes envolvidos.")
        elif set(ids) - clientes_relatorio:
            erros.append("Trecho de KM referencia cliente fora do relatório.")

    return list(dict.fromkeys(erros))


def _diagnostico_exception_salvamento(exc):
    mensagem = str(exc)
    if "relatorios_despesarateio" in mensagem or "relatorios_trechorateiokm" in mensagem:
        return {
            "tipo": exc.__class__.__name__,
            "codigo": "RATEIO_MIGRATION_PENDENTE",
            "mensagem": (
                "As tabelas de rateio ainda não existem no banco. "
                "Execute python manage.py migrate no ambiente."
            ),
        }
    return {
        "tipo": exc.__class__.__name__,
        "codigo": "ERRO_INTERNO_SALVAMENTO",
        "mensagem": "Erro interno ao salvar relatório. Verifique o log do servidor.",
    }


def _sync_equipe(relatorio, tecnicos_apoio):
    """Sincroniza técnicos de apoio do relatório (M2M via through)."""
    from .models import RelatorioTecnicoEquipe

    relatorio.equipe.exclude(tecnico__in=tecnicos_apoio).delete()
    existentes = set(relatorio.equipe.values_list("tecnico_id", flat=True))
    for tecnico in tecnicos_apoio:
        if tecnico.pk not in existentes:
            RelatorioTecnicoEquipe.objects.create(
                relatorio=relatorio,
                tecnico=tecnico,
            )


def _acao_relatorio_post(post_data):
    """
    Normaliza a acao do formulario.

    Rascunho deve ser uma escolha explicita do botao "Salvar rascunho".
    Qualquer POST sem acao, vindo por Enter, JS antigo ou browser sem
    `event.submitter`, segue o fluxo principal de envio para conferencia.
    """
    return "rascunho" if post_data.get("acao") == "rascunho" else "enviar"


def _label_botao_envio_relatorio(instance=None):
    if not instance:
        return "Criar Relatório"
    if instance.status == StatusRelatorio.AJUSTE:
        return "Reenviar para conferência"
    if instance.status == StatusRelatorio.RASCUNHO:
        return "Enviar para conferência"
    return "Salvar alterações"


def _duplicar_relatorio(original, usuario=None):
    novo = RelatorioTecnico(
        cliente=original.cliente,
        tecnico_responsavel=original.tecnico_responsavel,
        municipio_atendimento=original.municipio_atendimento,
        cidade_atendimento=original.cidade_atendimento,
        uf_atendimento=original.uf_atendimento,
        tipo_localidade=original.tipo_localidade,
        localidade_override=original.localidade_override,
        motivo_override_localidade=original.motivo_override_localidade,
        data_inicio=original.data_inicio,
        data_fim=original.data_fim,
        motivo=original.motivo,
        tipo_relatorio=original.tipo_relatorio,
        valor_adiantamento=original.valor_adiantamento or Decimal("0.00"),
        km_excedente_interno=original.km_excedente_interno or Decimal("0.00"),
        observacao_km_excedente=original.observacao_km_excedente,
        observacoes=original.observacoes,
        status=StatusRelatorio.RASCUNHO,
        criado_por=usuario,
    )
    novo.save()
    registrar_evento(
        novo,
        usuario,
        TipoEventoHistorico.CRIADO,
        f"Rascunho criado a partir da duplicação do relatório {original.identificador}.",
        {"origem_relatorio_id": original.pk, "origem_numero": original.numero},
    )

    for apoio in original.equipe.select_related("tecnico").all():
        RelatorioTecnicoEquipe.objects.create(
            relatorio=novo,
            tecnico=apoio.tecnico,
            papel=apoio.papel,
        )

    clientes_originais = list(original.clientes_vinculados.select_related("cliente").all())
    if not clientes_originais and original.cliente_id:
        novo.clientes_vinculados.create(
            cliente=original.cliente,
            ordem=1,
            motivo_viagem=original.motivo or "",
        )
    for vinculo in clientes_originais:
        novo.clientes_vinculados.create(
            cliente=vinculo.cliente,
            ordem=vinculo.ordem,
            motivo_viagem=vinculo.motivo_viagem or original.motivo or "",
        )

    despesas_originais = list(
        original.despesas.prefetch_related("clientes_vinculados__cliente").all()
    )
    despesas = [
        ItemDespesa(
            relatorio=novo,
            ordem=despesa.ordem,
            data=None,
            tipo=despesa.tipo,
            descricao=despesa.descricao,
            valor=despesa.valor,
            valor_aprovado=None,
            quem_pagou=despesa.quem_pagou,
            comprovante=None,
            observacoes=despesa.observacoes,
        )
        for despesa in despesas_originais
    ]
    if despesas:
        despesas_criadas = ItemDespesa.objects.bulk_create(despesas)
        for despesa_original, despesa_nova in zip(despesas_originais, despesas_criadas):
            vinculos_item = list(despesa_original.clientes_vinculados.all())
            if not vinculos_item and original.cliente_id:
                despesa_nova.clientes_vinculados.create(cliente=original.cliente)
            for vinculo in vinculos_item:
                despesa_nova.clientes_vinculados.create(cliente=vinculo.cliente)

    trechos_originais = list(
        original.trechos.prefetch_related("clientes_vinculados__cliente").all()
    )
    trechos = [
        TrechoKm(
            relatorio=novo,
            ordem=trecho.ordem,
            data=None,
            origem=trecho.origem,
            origem_endereco_completo=trecho.origem_endereco_completo,
            origem_lat=trecho.origem_lat,
            origem_lon=trecho.origem_lon,
            destino=trecho.destino,
            destino_endereco_completo=trecho.destino_endereco_completo,
            destino_lat=trecho.destino_lat,
            destino_lon=trecho.destino_lon,
            km=trecho.km,
            km_calculado_api=trecho.km_calculado_api,
            km_informado=trecho.km_informado,
            diferenca_km_percentual=trecho.diferenca_km_percentual,
            fonte_calculo_rota=trecho.fonte_calculo_rota,
            calculado_em=trecho.calculado_em,
            rota_geojson=trecho.rota_geojson or {},
            valor_km=valor_km_control_sul(),
            valor_km_aprovado=None,
            comprovante=None,
            observacao=trecho.observacao,
        )
        for trecho in trechos_originais
    ]
    for trecho in trechos:
        trecho.valor_calculado = (trecho.km * trecho.valor_km).quantize(
            Decimal("0.01")
        )
    if trechos:
        trechos_criados = TrechoKm.objects.bulk_create(trechos)
        for trecho_original, trecho_novo in zip(trechos_originais, trechos_criados):
            vinculos_item = list(trecho_original.clientes_vinculados.all())
            if not vinculos_item and original.cliente_id:
                trecho_novo.clientes_vinculados.create(cliente=original.cliente)
            for vinculo in vinculos_item:
                trecho_novo.clientes_vinculados.create(cliente=vinculo.cliente)

    return novo


def _snapshot_geo_trecho(trecho):
    if not trecho:
        return None
    return {
        "km": trecho.km,
        "km_calculado_api": trecho.km_calculado_api,
        "km_informado": trecho.km_informado,
        "diferenca_km_percentual": trecho.diferenca_km_percentual,
        "fonte_calculo_rota": trecho.fonte_calculo_rota,
        "rota_geojson": trecho.rota_geojson or {},
    }


def _snapshot_km_excedente(relatorio):
    if not relatorio:
        return None
    return {
        "km": relatorio.km_excedente_interno or Decimal("0.00"),
        "observacao": relatorio.observacao_km_excedente or "",
    }


def _registrar_auditoria_km_excedente(relatorio, usuario, anterior=None):
    anterior = anterior or {"km": Decimal("0.00"), "observacao": ""}
    km_anterior = anterior.get("km") or Decimal("0.00")
    km_atual = relatorio.km_excedente_interno or Decimal("0.00")
    obs_anterior = anterior.get("observacao") or ""
    obs_atual = relatorio.observacao_km_excedente or ""

    if km_anterior == km_atual and obs_anterior == obs_atual:
        return

    if km_anterior <= 0 and km_atual > 0:
        descricao = "KM excedente / deslocamento interno criado."
    elif km_anterior > 0 and km_atual <= 0:
        descricao = "KM excedente / deslocamento interno removido."
    else:
        descricao = "KM excedente / deslocamento interno alterado."

    registrar_evento(
        relatorio,
        usuario,
        TipoEventoHistorico.VALOR_ALTERADO,
        descricao,
        {
            "valor_anterior": str(km_anterior),
            "valor_novo": str(km_atual),
            "observacao_anterior": obs_anterior,
            "observacao_nova": obs_atual,
            "clientes_impactados": [
                {
                    "cliente_id": linha["cliente"].pk,
                    "cliente_nome": linha["cliente"].nome,
                    "km": str(linha["km"]),
                    "valor_km": str(linha["valor_km"]),
                    "valor_calculado": str(linha["valor_calculado"]),
                }
                for linha in relatorio.rateio_km_excedente_clientes()
            ],
        },
    )


def _registrar_auditoria_geografica_trecho(relatorio, usuario, trecho, anterior=None):
    dados = {
        "trecho_id": trecho.pk,
        "origem": trecho.origem,
        "destino": trecho.destino,
        "origem_endereco_completo": trecho.origem_endereco_completo,
        "destino_endereco_completo": trecho.destino_endereco_completo,
        "km_calculado_api": str(trecho.km_calculado_api or ""),
        "km_informado": str(trecho.km_informado or trecho.km or ""),
        "diferenca_km_percentual": str(trecho.diferenca_km_percentual or ""),
        "fonte_calculo_rota": trecho.fonte_calculo_rota or "",
        "anterior": {
            chave: str(valor or "")
            for chave, valor in (anterior or {}).items()
        },
    }
    diferenca_anterior = (anterior or {}).get("diferenca_km_percentual") or Decimal("0.00")

    if trecho.km_calculado_api and not (anterior or {}).get("km_calculado_api"):
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "Rota de KM calculada automaticamente.",
            dados,
        )

    if trecho.km_calculado_api and anterior and anterior.get("km") != trecho.km:
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado alterado manualmente após cálculo de rota.",
            dados,
        )

    if trecho.km_divergente_rota and diferenca_anterior <= Decimal("15.00"):
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado difere mais de 15% da rota calculada.",
            dados,
        )

    if not trecho.km_calculado_api and trecho.km and not anterior:
        registrar_evento(
            relatorio,
            usuario,
            TipoEventoHistorico.VALOR_ALTERADO,
            "KM informado manualmente sem rota calculada.",
            dados,
        )


def _mapa_trechos_relatorio(relatorio):
    dados = []
    for ordem, trecho in enumerate(relatorio.trechos.all(), start=1):
        if not all([trecho.origem_lat, trecho.origem_lon, trecho.destino_lat, trecho.destino_lon]):
            continue
        clientes = [rateio.cliente.nome for rateio in trecho.rateios.all()] or [
            vinculo.cliente.nome for vinculo in trecho.clientes_vinculados.all()
        ]
        dados.append(
            {
                "ordem": ordem,
                "origem": trecho.origem_endereco_completo or trecho.origem,
                "destino": trecho.destino_endereco_completo or trecho.destino,
                "origem_lat": str(trecho.origem_lat),
                "origem_lon": str(trecho.origem_lon),
                "destino_lat": str(trecho.destino_lat),
                "destino_lon": str(trecho.destino_lon),
                "rota_geojson": trecho.rota_geojson or {},
                "km_calculado": str(trecho.km_calculado_api or ""),
                "km_informado": str(trecho.km_informado or trecho.km or ""),
                "diferenca_percentual": str(trecho.diferenca_km_percentual or ""),
                "divergente": trecho.km_divergente_rota,
                "clientes": clientes,
            }
        )
    return dados


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────


def _formatar_data(data):
    return data.strftime("%d/%m/%Y") if data else "-"


def _formatar_moeda(valor):
    valor = valor or Decimal("0.00")
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _request_espera_json(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


def _lista_erros_operacionais(exc):
    erros = getattr(exc, "errors", None)
    if erros:
        return list(erros)
    return [str(exc)]


def _adicionar_erros_operacionais(request, erros):
    for erro in erros:
        messages.error(request, erro, extra_tags="operational-error")


def _itens_pdf_reembolso(relatorio):
    itens = []

    for despesa in relatorio.despesas.all():
        valor = despesa.valor_aprovado
        if valor is None:
            valor = despesa.valor
        valor = (valor or Decimal("0.00")).quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        itens.append(
            {
                "data": despesa.data,
                "documento": "Comprovante",
                "descricao": despesa.descricao,
                "valor": valor,
            }
        )

    for trecho in relatorio.trechos.all():
        valor = trecho.valor_final
        valor = (valor or Decimal("0.00")).quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        itens.append(
            {
                "data": trecho.data,
                "documento": "Comprovante",
                "descricao": "Deslocamento",
                "valor": valor,
            }
        )

    itens.sort(key=lambda item: item["data"] or relatorio.data_inicio)
    return itens


def _usuario_pode_aprovar_financeiro(user):
    return usuario_pode_atuar_como_financeiro(user)


@require_GET
@login_required
def politica_despesa_json(request):
    tipo = (request.GET.get("tipo") or "").strip()
    data_txt = (request.GET.get("data") or "").strip()
    tipo_localidade = (request.GET.get("tipo_localidade") or "").strip()
    municipio = None
    municipio_id = (request.GET.get("municipio_id") or "").strip()
    if municipio_id:
        municipio = Municipio.objects.filter(pk=municipio_id, ativo=True).first()
        if municipio:
            tipo_localidade = municipio.tipo_localidade_padrao
    if not tipo or not data_txt:
        return JsonResponse(
            {"success": True, "data": {"limite": None, "mensagem": "Sem politica definida"}}
        )
    data_ref = parse_date(data_txt)
    if not data_ref:
        return JsonResponse(
            {"success": False, "error": "Data invalida para consultar politica."},
            status=400,
        )
    from relatorios.services.politica_valor_service import resolver_politica_despesa

    politica = resolver_politica_despesa(
        tipo_despesa=tipo,
        data=data_ref,
        tipo_localidade=tipo_localidade,
        cidade=(request.GET.get("cidade") or "").strip(),
        municipio=municipio,
        descricao=(request.GET.get("descricao") or "").strip(),
        valor_informado=request.GET.get("valor") or "0",
    )
    if politica is None:
        logger.warning(
            "politica_despesa_nao_encontrada tipo=%s data=%s localidade=%s usuario=%s",
            tipo,
            data_txt,
            tipo_localidade,
            getattr(request.user, "pk", None),
        )
        return JsonResponse(
            {"success": True, "data": {"limite": None, "mensagem": "Sem politica definida"}}
        )
    return JsonResponse(
        {
            "success": True,
            "data": {
                "chave": politica.chave,
                "descricao": politica.descricao,
                "limite": str(politica.valor),
                "excede": politica.excede,
                "excesso": str(politica.excesso),
                "mensagem": f"{politica.descricao} - R$ {politica.valor:.2f}",
            },
        }
    )


@require_GET
@login_required
def municipios_buscar_json(request):
    termo = (request.GET.get("q") or "").strip()
    if len(termo) < 2:
        return JsonResponse({"success": True, "data": []})

    from .models import normalizar_texto_busca

    termo_limpo = termo.replace("/", " ").replace(",", " ")
    partes = termo_limpo.split()
    uf_informada = ""
    if len(partes) > 1 and len(partes[-1]) == 2:
        uf_informada = partes[-1].upper()
        termo_limpo = " ".join(partes[:-1]) or termo_limpo
    termo_norm = normalizar_texto_busca(termo_limpo)
    qs = Municipio.objects.filter(ativo=True)
    filtros = (
        Q(nome_normalizado__icontains=termo_norm)
        | Q(nome__icontains=termo_limpo)
        | Q(aliases__icontains=termo_limpo)
        | Q(aliases__icontains=termo_norm)
    )
    if len(termo.strip()) == 2:
        filtros |= Q(uf__iexact=termo)
    qs = qs.filter(filtros)
    if uf_informada:
        qs = qs.filter(uf__iexact=uf_informada)
    municipios = list(qs.order_by("nome", "uf")[:40])
    municipios.sort(
        key=lambda municipio: (
            municipio.nome_normalizado != termo_norm,
            not municipio.nome_normalizado.startswith(termo_norm),
            municipio.nome,
            municipio.uf,
        )
    )
    dados = [
        {
            "id": municipio.pk,
            "codigo_ibge": municipio.codigo_ibge,
            "nome": municipio.nome,
            "uf": municipio.uf,
            "uf_nome": municipio.uf_nome,
            "tipo_localidade": municipio.tipo_localidade_padrao,
            "tipo_localidade_label": municipio.get_tipo_localidade_padrao_display(),
            "label": municipio.label,
        }
        for municipio in municipios[:20]
    ]
    return JsonResponse({"success": True, "data": dados})


def _relatorio_bloqueado(relatorio):
    return workflow_relatorio_bloqueado(relatorio)


def _relatorio_editavel_por_usuario(relatorio, user):
    return usuario_pode_editar_relatorio(user, relatorio)


def _relatorios_visiveis(user, queryset=None):
    queryset = queryset if queryset is not None else RelatorioTecnico.objects.all()
    return queryset_relatorios_visiveis(user, queryset)


def _usuario_pode_ver_relatorio_ou_403(user, relatorio):
    if not usuario_pode_visualizar_relatorio(user, relatorio):
        raise RelatorioTecnico.DoesNotExist


def _relatorio_filtro_form(user, data=None):
    form = RelatorioFiltroForm(data)
    if not usuario_eh_administrativo(user):
        relatorios = _relatorios_visiveis(user, RelatorioTecnico.objects.all())
        form.fields["tecnico"].queryset = Tecnico.objects.filter(
            pk__in=relatorios.values("tecnico_responsavel_id")
        )
        form.fields["cliente"].queryset = Cliente.objects.filter(
            pk__in=relatorios.values("cliente_id")
        )
    return form

class AcessoErpMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):

        print("DEBUG USER:", request.user)
        print("DEBUG AUTH:", request.user.is_authenticated)

        # deixa o LoginRequiredMixin agir primeiro
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        print("DEBUG GROUPS:", list(request.user.groups.values_list("name", flat=True)))
        print("DEBUG SUPERUSER:", request.user.is_superuser)
        print("DEBUG ERP:", usuario_pode_acessar_erp(request.user))

        if not usuario_pode_acessar_erp(request.user):
            messages.error(request, "Seu usuário não possui perfil de acesso ao ERP.")
            raise PermissionDenied("Usuário sem grupo ERP.")

        return super().dispatch(request, *args, **kwargs)


class AdministrativoMixin(AcessoErpMixin):
    def dispatch(self, request, *args, **kwargs):
        if not usuario_eh_administrativo(request.user):
            messages.error(request, "Você não tem permissão para acessar esta área.")
            return redirect("relatorios:dashboard")
        return super().dispatch(request, *args, **kwargs)


def _avisos_financeiro(relatorio):
    avisos = []
    despesas = list(relatorio.despesas.all())
    trechos = list(relatorio.trechos.all())

    despesas_por_data = {}
    despesas_duplicadas = {}
    despesas_sem_comprovante = []
    despesas_altas_sem_observacao = []

    for idx, despesa in enumerate(despesas, start=1):
        linha_id = f"linha-despesa-{idx}"
        descricao = (despesa.descricao or "").strip()
        observacoes = (despesa.observacoes or "").strip()

        if descricao and len(descricao) < 4:
            avisos.append(
                {
                    "tipo": "descricao_curta",
                    "mensagem": f"Verificar item {idx}: descrição pode não ter sido preenchida corretamente.",
                    "linha_id": linha_id,
                    "linha_ids": [linha_id],
                    "icone": "bi-pencil-square",
                }
            )

        if despesa.tipo == "alimentacao" and despesa.data:
            despesas_por_data.setdefault(despesa.data, []).append((idx, despesa))

        if (
            despesa.tipo
            not in {TipoDespesa.PASSAGEM, TipoDespesa.HOSPEDAGEM, TipoDespesa.TRANSPORTE}
            and despesa.valor
            and despesa.valor > Decimal("300.00")
            and not observacoes
        ):
            despesas_altas_sem_observacao.append((idx, despesa))

        if despesa.acima_politica:
            avisos.append(
                {
                    "tipo": "despesa_acima_politica",
                    "mensagem": (
                        f"Despesa {idx} acima da politica vigente. "
                        f"Solicitado R$ {despesa.valor:.2f}; politica R$ {despesa.valor_politica:.2f}."
                    ),
                    "linha_id": linha_id,
                    "linha_ids": [linha_id],
                    "icone": "bi-exclamation-triangle",
                }
            )
            logger.info(
                "despesa_acima_politica relatorio=%s despesa=%s valor=%s politica=%s",
                relatorio.pk,
                despesa.pk,
                despesa.valor,
                despesa.valor_politica,
            )

        if not despesa.comprovante:
            despesas_sem_comprovante.append((idx, despesa))

        chave_duplicada = (despesa.data, despesa.tipo, despesa.valor)
        despesas_duplicadas.setdefault(chave_duplicada, []).append((idx, despesa))

    if despesas_altas_sem_observacao:
        linha_ids = [
            f"linha-despesa-{idx}" for idx, _despesa in despesas_altas_sem_observacao
        ]
        for idx, _despesa in despesas_altas_sem_observacao:
            linha_id = f"linha-despesa-{idx}"
            avisos.append(
                {
                    "tipo": "despesa_alta_sem_observacao",
                    "mensagem": "Despesa alta sem detalhamento em observações.",
                    "linha_id": linha_id,
                    "linha_ids": linha_ids,
                    "icone": "bi-cash-coin",
                }
            )

    for data, itens in despesas_por_data.items():
        if len(itens) >= 4:
            ultimo_idx = itens[-1][0]
            linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in itens]
            avisos.append(
                {
                    "tipo": "muitas_refeicoes",
                    "mensagem": f"O usuário incluiu {len(itens)} refeições na data {_formatar_data(data)}.",
                    "linha_id": f"linha-despesa-{ultimo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-cup-hot",
                }
            )

    if despesas_sem_comprovante:
        primeiro_idx = despesas_sem_comprovante[0][0]
        linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in despesas_sem_comprovante]
        avisos.append(
            {
                "tipo": "falta_comprovante",
                "mensagem": f"{len(despesas_sem_comprovante)} despesas foram enviadas sem comprovante.",
                "linha_id": f"linha-despesa-{primeiro_idx}",
                "linha_ids": linha_ids,
                "icone": "bi-paperclip",
            }
        )

    for itens in despesas_duplicadas.values():
        if len(itens) >= 2:
            segundo_idx = itens[1][0]
            linha_ids = [f"linha-despesa-{idx}" for idx, _despesa in itens]
            avisos.append(
                {
                    "tipo": "despesa_duplicada",
                    "mensagem": "Possível despesa duplicada encontrada.",
                    "linha_id": f"linha-despesa-{segundo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-files",
                }
            )

    trechos_por_data = {}
    trechos_km_rota_divergente = []

    for idx, trecho in enumerate(trechos, start=1):
        linha_id = f"linha-trecho-{idx}"

        if trecho.data:
            trechos_por_data.setdefault(trecho.data, []).append((idx, trecho))

        if trecho.km_divergente_rota:
            trechos_km_rota_divergente.append((idx, trecho))

    if trechos_km_rota_divergente:
        linha_ids = [
            f"linha-trecho-{idx}" for idx, _trecho in trechos_km_rota_divergente
        ]
        for idx, _trecho in trechos_km_rota_divergente:
            linha_id = f"linha-trecho-{idx}"
            avisos.append(
                {
                    "tipo": "km_rota_divergente",
                    "mensagem": "KM informado difere mais de 15% da rota calculada.",
                    "linha_id": linha_id,
                    "linha_ids": linha_ids,
                    "icone": "bi-signpost-split",
                }
            )

    for data, itens in trechos_por_data.items():
        if len(itens) >= 5:
            ultimo_idx = itens[-1][0]
            linha_ids = [f"linha-trecho-{idx}" for idx, _trecho in itens]
            avisos.append(
                {
                    "tipo": "muitos_deslocamentos",
                    "mensagem": f"O usuário incluiu muitos deslocamentos na data {_formatar_data(data)}.",
                    "linha_id": f"linha-trecho-{ultimo_idx}",
                    "linha_ids": linha_ids,
                    "icone": "bi-geo-alt",
                }
            )

    return avisos


class DashboardView(AcessoErpMixin, TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_dashboard_context(self.request.user, self.request.GET))
        ctx["titulo_pagina"] = "Dashboard"
        return ctx


@require_GET
@login_required
@exigir_acesso_erp
def dashboard_dados_json(request):
    inicio = time.perf_counter()
    dados = get_dashboard_data(request.user, request.GET)
    duracao = time.perf_counter() - inicio
    if duracao > 2:
        logger.warning(
            "Endpoint JSON do dashboard lento. usuario=%s duracao=%.2fs",
            request.user.pk,
            duracao,
        )
    return JsonResponse(dados)


def _relatorios_legados_visiveis(user):
    qs = RelatorioLegado.objects.select_related("cliente_vinculado", "tecnico_vinculado", "importado_por")
    if usuario_pode_atuar_como_financeiro(user) or usuario_eh_administrativo(user):
        return qs
    nome_usuario = normalizar_nome_pessoa(user.get_full_name() or user.username)
    if not nome_usuario:
        return qs.none()
    return qs.filter(tecnico_nome_normalizado=nome_usuario)


class RelatorioLegadoListView(AcessoErpMixin, ListView):
    model = RelatorioLegado
    template_name = "relatorios/legados_list.html"
    context_object_name = "relatorios"
    paginate_by = 20

    def get_queryset(self):
        qs = _relatorios_legados_visiveis(self.request.user).prefetch_related("despesas")
        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            qs = qs.filter(
                Q(numero_original_legado__icontains=busca)
                | Q(cliente_nome__icontains=busca)
                | Q(tecnico_nome__icontains=busca)
                | Q(cidade__icontains=busca)
                | Q(motivo__icontains=busca)
            )
        return qs.order_by("-data_inicio", "-importado_em", "-numero_original_legado")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["query"] = self.request.GET.get("q", "")
        ctx["total"] = self.get_queryset().count()
        ctx["pagination_query"] = params.urlencode()
        ctx["titulo_pagina"] = "Relatórios legados"
        return ctx


@login_required
@exigir_acesso_erp
def relatorio_legado_detail_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_legados_visiveis(request.user).prefetch_related("despesas"),
        pk=pk,
    )
    despesas = list(relatorio.despesas.all())
    colunas_despesas = {
        "data": any(item.data or item.data_original for item in despesas),
        "documento": any(item.documento for item in despesas),
        "descricao": any(item.descricao for item in despesas),
        "tipo": any(item.tipo_descricao or item.tipo_codigo for item in despesas),
        "valor": any(item.valor for item in despesas),
    }
    km_legado = getattr(relatorio, "km_legado", None)
    return render(
        request,
        "relatorios/legado_detail.html",
        {
            "relatorio": relatorio,
            "despesas": despesas,
            "colunas_despesas": colunas_despesas,
            "km_legado": km_legado,
            "titulo_pagina": f"Relatório legado #{relatorio.numero_original_legado}",
        },
    )


class RelatorioListView(AcessoErpMixin, ListView):
    model = RelatorioTecnico
    template_name = "relatorios/relatorio_list.html"
    context_object_name = "relatorios"
    paginate_by = 15

    def get_queryset(self):
        qs = _relatorios_visiveis(
            self.request.user,
            RelatorioTecnico.objects.select_related(
                "cliente", "tecnico_responsavel", "aprovado_por", "criado_por", "snapshot_financeiro"
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "equipe__tecnico",
                "despesas",
                "trechos",
            ),
        )
        form = _relatorio_filtro_form(self.request.user, self.request.GET)
        if form.is_valid():
            cd = form.cleaned_data
            if cd.get("tecnico"):
                qs = qs.filter(tecnico_responsavel=cd["tecnico"])
            if cd.get("cliente"):
                qs = qs.filter(cliente=cd["cliente"])
            if cd.get("status"):
                qs = qs.filter(status=cd["status"])
            if cd.get("data_inicio"):
                qs = qs.filter(data_inicio__gte=cd["data_inicio"])
            if cd.get("data_fim"):
                qs = qs.filter(data_fim__lte=cd["data_fim"])
            if cd.get("busca"):
                q = cd["busca"]
                q_digits = re.sub(r"\D+", "", q)
                qs = qs.filter(
                    Q(numero__icontains=q)
                    | Q(cliente__nome__icontains=q)
                    | Q(cliente__razao_social__icontains=q)
                    | Q(cliente__nome_fantasia__icontains=q)
                    | Q(cliente__cnpj_cpf__icontains=q_digits or q)
                    | Q(tecnico_responsavel__nome__icontains=q)
                    | Q(cidade_atendimento__icontains=q)
                )
        sort = self.request.GET.get("sort")
        direction = self.request.GET.get("dir")
        if sort == "numero":
            campo_numero = "-numero" if direction == "desc" else "numero"
            return qs.order_by(campo_numero, "-criado_em")
        return qs.order_by("-criado_em")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        params_sem_ordem = params.copy()
        params_sem_ordem.pop("sort", None)
        params_sem_ordem.pop("dir", None)
        sort_atual = self.request.GET.get("sort")
        direcao_atual = self.request.GET.get("dir")
        proxima_direcao_numero = (
            "desc" if sort_atual == "numero" and direcao_atual != "desc" else "asc"
        )
        params_numero = params_sem_ordem.copy()
        params_numero["sort"] = "numero"
        params_numero["dir"] = proxima_direcao_numero
        ctx["form_filtro"] = _relatorio_filtro_form(self.request.user, self.request.GET)
        ctx["titulo_pagina"] = "Relatórios Técnicos"
        ctx["total"] = self.get_queryset().count()
        ctx["sort_atual"] = sort_atual
        ctx["direcao_atual"] = direcao_atual
        ctx["numero_sort_url"] = "?" + params_numero.urlencode()
        ctx["pagination_query"] = params.urlencode()
        return ctx


# ─────────────────────────────────────────────
# CRIAR / EDITAR
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def relatorio_form_view(request, pk=None):
    """
    View única para criar e editar relatórios.

    Regra de nomenclatura:
    - `valor_km`        → campo real no model Cliente (banco de dados)
    - `valor_km_padrao` → variável auxiliar local, repassada ao TrechoKmForm
                          via kwargs, e ao template para exibição/JS.
                          NUNCA é gravada no banco diretamente.
    """
    instance = (
        get_object_or_404(
            _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
            pk=pk,
        )
        if pk
        else None
    )
    if instance and not _relatorio_editavel_por_usuario(instance, request.user):
        if instance.status == StatusRelatorio.AJUSTE:
            messages.error(request, "Relatório em ajuste deve ser editado pelo técnico.")
        else:
            messages.error(request, "Relatório aprovado ou rejeitado não pode ser editado.")
        return redirect("relatorios:relatorio_detail", pk=instance.pk)

    resumo_erros = []

    # ── Determinar valor_km_padrao (variável auxiliar) ────────────────────────
    # No POST: lê o cliente enviado no form para recalcular o padrão correto.
    # No GET:  lê o cliente já associado ao relatório (edição) ou 0 (criação).
    # Isso garante que linhas de KM novas já recebam o valor inicial correto.
    if request.method == "POST":
        clientes_post_ids, clientes_post_nomes = _clientes_selecionados_do_request(
            request,
            instance,
        )
        tecnicos_post_ids, tecnicos_post_nomes = _tecnicos_selecionados_do_request(
            request,
            instance,
        )
        motivos_clientes = _motivos_clientes_do_request(request, instance)
        cliente_id = clientes_post_ids[0] if clientes_post_ids else request.POST.get("cliente")
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view POST: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )
    else:
        clientes_post_ids, clientes_post_nomes = _clientes_selecionados_do_request(
            request,
            instance,
        )
        tecnicos_post_ids, tecnicos_post_nomes = _tecnicos_selecionados_do_request(
            request,
            instance,
        )
        motivos_clientes = _motivos_clientes_do_request(request, instance)
        cliente_id = getattr(instance, "cliente_id", None) if instance else None
        valor_km_padrao = _get_valor_km_para_cliente(cliente_id)
        logger.debug(
            "relatorio_form_view GET: cliente_id=%s, valor_km_padrao=%s",
            cliente_id,
            valor_km_padrao,
        )

    # ── POST ──────────────────────────────────────────────────────────────────
    if request.method == "POST":
        form = RelatorioTecnicoForm(
            request.POST,
            request.FILES,
            instance=instance,
        )

        fs_desp = ItemDespesaFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="despesas",
        )

        fs_km = TrechoKmFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="trechos",
            form_kwargs={"valor_km_padrao": valor_km_padrao},
        )
        _popular_clientes_formsets_para_template(request, fs_desp, fs_km)

        form_ok = form.is_valid()
        desp_ok = fs_desp.is_valid()
        km_ok = fs_km.is_valid()

        logger.debug(
            "Validação: form=%s | fs_desp=%s | fs_km=%s",
            form_ok,
            desp_ok,
            km_ok,
        )
        if not form_ok:
            logger.debug("Erros form principal: %s", form.errors)
        if not desp_ok:
            logger.debug("Erros fs_desp: %s", fs_desp.errors)
        if not km_ok:
            logger.debug("Erros fs_km: %s", fs_km.errors)

        if form_ok and desp_ok and km_ok:
            cliente_ids_relatorio = normalizar_ids_clientes(
                request.POST.get("clientes_relatorio")
            )
            erros_clientes = _validar_clientes_formsets(
                request,
                fs_desp,
                fs_km,
                cliente_ids_relatorio,
            )
            if erros_clientes:
                resumo_erros.extend(erros_clientes)
                form_ok = False

        if form_ok and desp_ok and km_ok:
            # ── Determinar ação ───────────────────────────────────────────────
            # "acao" vem do name/value do botão clicado:
            #   "rascunho" → botão Salvar rascunho
            #   "enviar"   → botão Salvar relatório (ou confirmação do modal)
            acao = _acao_relatorio_post(request.POST)
            relatorio = form.save(commit=False)
            logger.info(
                "RELATORIO_SAVE_DEBUG etapa=commit_false acao=%s user_id=%s username=%s "
                "relatorio_id=%s status=%s criado_por_id=%s tecnico_responsavel_id=%s "
                "tecnico_reembolso_id=%s instance_id=%s",
                acao,
                getattr(request.user, "pk", None),
                getattr(request.user, "username", ""),
                getattr(relatorio, "pk", None),
                getattr(relatorio, "status", None),
                getattr(relatorio, "criado_por_id", None),
                getattr(relatorio, "tecnico_responsavel_id", None),
                getattr(relatorio, "tecnico_reembolso_id", None),
                getattr(instance, "pk", None),
            )
            if acao != "rascunho" and not relatorio.municipio_atendimento_id:
                form.add_error(
                    "cidade_atendimento",
                    "Selecione uma cidade válida da lista para aplicar corretamente a política de valores.",
                )
                form_ok = False
                resumo_erros.append(
                    "Selecione uma cidade oficial para enviar o relatório à conferência."
                )

            relatorio = preparar_rascunho_para_salvar(relatorio, instance)

            erros_extras = False

            # ── Salvar ────────────────────────────────────────────────────────
            # Condição limpa: só usa a flag local + form.errors do principal.
            # NÃO re-checa fs_desp.errors nem fs_km.errors aqui — eles já
            # foram validados pelo is_valid() acima e qualquer erro extra
            # foi capturado pela flag `erros_extras`.
            if not erros_extras and not form.errors:
                try:
                    with transaction.atomic():
                        relatorio_novo = instance is None
                        if not relatorio_novo:
                            relatorio_atual = RelatorioTecnico.objects.select_for_update().get(
                                pk=instance.pk
                            )
                            if not _relatorio_editavel_por_usuario(relatorio_atual, request.user):
                                raise WorkflowError(
                                    "Este relatório foi alterado por outro usuário e não pode mais ser editado."
                                )
                            km_excedente_anterior = _snapshot_km_excedente(relatorio_atual)
                            relatorio.status = relatorio_atual.status
                        else:
                            km_excedente_anterior = _snapshot_km_excedente(None)
                        if relatorio_novo:
                            relatorio.criado_por = request.user
                        relatorio.cliente_id = cliente_ids_relatorio[0]
                        logger.info(
                            "RELATORIO_SAVE_DEBUG etapa=antes_save acao=%s novo=%s user_id=%s "
                            "username=%s relatorio_id=%s status=%s criado_por_id=%s "
                            "tecnico_responsavel_id=%s tecnico_reembolso_id=%s",
                            acao,
                            relatorio_novo,
                            getattr(request.user, "pk", None),
                            getattr(request.user, "username", ""),
                            getattr(relatorio, "pk", None),
                            getattr(relatorio, "status", None),
                            getattr(relatorio, "criado_por_id", None),
                            getattr(relatorio, "tecnico_responsavel_id", None),
                            getattr(relatorio, "tecnico_reembolso_id", None),
                        )
                        relatorio.save()
                        logger.info(
                            "RELATORIO_SAVE_DEBUG etapa=apos_save acao=%s novo=%s user_id=%s "
                            "username=%s relatorio_id=%s status=%s criado_por_id=%s "
                            "tecnico_responsavel_id=%s tecnico_reembolso_id=%s",
                            acao,
                            relatorio_novo,
                            getattr(request.user, "pk", None),
                            getattr(request.user, "username", ""),
                            getattr(relatorio, "pk", None),
                            getattr(relatorio, "status", None),
                            getattr(relatorio, "criado_por_id", None),
                            getattr(relatorio, "tecnico_responsavel_id", None),
                            getattr(relatorio, "tecnico_reembolso_id", None),
                        )
                        form.save_m2m()
                        sync_clientes_relatorio(
                            relatorio,
                            cliente_ids_relatorio,
                            motivos_clientes,
                        )

                        tecnicos_apoio = form.cleaned_data.get("tecnicos_equipe", [])
                        _sync_equipe(relatorio, tecnicos_apoio)
                        usuario_historico = (
                            request.user if request.user.is_authenticated else None
                        )
                        _registrar_auditoria_km_excedente(
                            relatorio,
                            usuario_historico,
                            km_excedente_anterior,
                        )

                        fs_desp.instance = relatorio
                        for f in fs_desp.forms:
                            if not _form_has_content(f):
                                continue
                            item = f.save(commit=False)
                            item.relatorio = relatorio
                            item.save()
                            _registrar_metadados_comprovante(
                                relatorio,
                                usuario_historico,
                                item,
                                f.cleaned_data.get("comprovante"),
                            )
                            erros_item = sync_clientes_despesa(
                                item,
                                _clientes_item_post(request, f.prefix),
                            )
                            if erros_item:
                                raise WorkflowError(erros_item)
                        for f in fs_desp.deleted_forms:
                            if f.instance.pk:
                                f.instance.delete()

                        fs_km.instance = relatorio
                        for f in fs_km.forms:
                            if not _form_has_content(f):
                                continue
                            trecho_anterior = (
                                _snapshot_geo_trecho(
                                    TrechoKm.objects.select_for_update().get(pk=f.instance.pk)
                                )
                                if f.instance.pk
                                else None
                            )
                            trecho = f.save(commit=False)
                            trecho.relatorio = relatorio
                            clientes_trecho = _clientes_item_post(request, f.prefix)
                            if len(clientes_trecho) == 1:
                                trecho.valor_km = Decimal(
                                    str(_get_valor_km_para_cliente(clientes_trecho[0]) or "0")
                                )
                            elif len(clientes_trecho) > 1:
                                trecho.valor_km = Decimal("0.00")
                            trecho.save()
                            _registrar_auditoria_geografica_trecho(
                                relatorio,
                                usuario_historico,
                                trecho,
                                trecho_anterior,
                            )
                            erros_trecho = sync_clientes_trecho(
                                trecho,
                                clientes_trecho,
                            )
                            if erros_trecho:
                                raise WorkflowError(erros_trecho)
                        for f in fs_km.deleted_forms:
                            if f.instance.pk:
                                f.instance.delete()

                        erros_integridade = validar_integridade_financeira_relatorio(
                            relatorio
                        )
                        if erros_integridade:
                            raise WorkflowError(erros_integridade)

                        if relatorio_novo:
                            registrar_evento(
                                relatorio,
                                usuario_historico,
                                TipoEventoHistorico.CRIADO,
                                f"Rascunho {relatorio.identificador} criado.",
                            )
                        if acao != "rascunho":
                            _log_envio_relatorio_pre_autorizacao(
                                request,
                                relatorio,
                                "relatorio_form_view",
                            )
                            relatorio = enviar_para_conferencia(
                                relatorio.pk,
                                usuario_historico,
                            )

                        logger.info(
                            "Relatório %s salvo (pk=%s, status=%s, acao=%s).",
                            relatorio.identificador,
                            relatorio.pk,
                            relatorio.status,
                            acao,
                        )

                    messages.success(
                        request,
                        f"Relatório {relatorio.identificador} salvo com sucesso.",
                    )
                    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

                except WorkflowError as exc:
                    erros = _lista_erros_operacionais(exc)
                    _adicionar_erros_operacionais(request, erros)
                    resumo_erros.extend(erros)
                    return render(
                        request,
                        "relatorios/relatorio_form.html",
                        {
                            "form": form,
                            "fs_desp": fs_desp,
                            "fs_km": fs_km,
                            "instance": instance,
                            "clientes_importacao": _clientes_queryset_selecao(),
                            "tecnicos_importacao": Tecnico.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "titulo_pagina": (
                                f"Editar Relatório {instance.identificador}"
                                if instance
                                else "Novo Relatório"
                            ),
                            "salvar_rascunho": "Salvar rascunho",
                            "enviar": _label_botao_envio_relatorio(instance),
                            "valor_km_padrao": valor_km_padrao,
                            "valor_km_control_sul": str(valor_km_control_sul()),
                            "resumo_erros": resumo_erros,
                            "clientes_selecionados_ids": clientes_post_ids,
                            "clientes_selecionados_nomes": clientes_post_nomes,
                            "motivos_clientes_relatorio": motivos_clientes,
                            "tecnicos_selecionados_ids": tecnicos_post_ids,
                            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
                        },
                    )
                except Exception as exc:
                    logger.exception("Erro ao salvar relatório: %s", exc)
                    messages.error(request, "Erro interno ao salvar. Tente novamente.")
                    # Não adiciona ao resumo_erros — é erro de infra, não de validação
                    return render(
                        request,
                        "relatorios/relatorio_form.html",
                        {
                            "form": form,
                            "fs_desp": fs_desp,
                            "fs_km": fs_km,
                            "instance": instance,
                            "clientes_importacao": _clientes_queryset_selecao(),
                            "tecnicos_importacao": Tecnico.objects.filter(
                                ativo=True
                            ).order_by("nome"),
                            "titulo_pagina": (
                                f"Editar Relatório {instance.identificador}"
                                if instance
                                else "Novo Relatório"
                            ),
                            "enviar": _label_botao_envio_relatorio(instance),
                            "valor_km_padrao": str(valor_km_padrao),
                            "valor_km_control_sul": str(valor_km_control_sul()),
                            "resumo_erros": [diagnostico_backend["mensagem"]],
                            "diagnostico_backend": diagnostico_backend,
                            "clientes_selecionados_ids": clientes_post_ids,
                            "clientes_selecionados_nomes": clientes_post_nomes,
                            "motivos_clientes_relatorio": motivos_clientes,
                            "tecnicos_selecionados_ids": tecnicos_post_ids,
                            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
                        },
                    )

        # ── Chegou aqui = alguma validação falhou ─────────────────────────────
        messages.error(request, "Corrija os erros indicados antes de salvar.")

        if not form_ok:
            resumo_erros.append(
                "Dados Gerais possui campos inválidos ou obrigatórios não preenchidos."
            )
        if not desp_ok:
            qtd = sum(1 for e in fs_desp.errors if e)
            resumo_erros.append(f"Despesas possui {qtd} inconsistência(s).")
        if not km_ok:
            qtd = sum(1 for e in fs_km.errors if e)
            resumo_erros.append(f"Trechos/KM possui {qtd} inconsistência(s).")

    # ── GET ───────────────────────────────────────────────────────────────────
    else:
        form = RelatorioTecnicoForm(instance=instance)

        fs_desp = ItemDespesaFormSet(
            instance=instance,
            prefix="despesas",
        )

        fs_km = TrechoKmFormSet(
            instance=instance,
            prefix="trechos",
            form_kwargs={"valor_km_padrao": valor_km_padrao},
        )
        _popular_clientes_formsets_para_template(request, fs_desp, fs_km)

    # ── Renderização (GET e POST com erro chegam aqui) ─────────────────────────
    return render(
        request,
        "relatorios/relatorio_form.html",
        {
            "form": form,
            "fs_desp": fs_desp,
            "fs_km": fs_km,
            "instance": instance,
            "clientes_importacao": _clientes_queryset_selecao(),
            "tecnicos_importacao": Tecnico.objects.filter(ativo=True).order_by("nome"),
            "titulo_pagina": (
                f"Editar Relatório {instance.identificador}" if instance else "Novo Relatório"
            ),
            "salvar_rascunho": "Salvar rascunho",
            # valor_km_padrao aqui é APENAS para uso no template (JS, exibição).
            # Sempre string para evitar erros de template com None.
            "enviar": _label_botao_envio_relatorio(instance),
            "valor_km_padrao": str(valor_km_padrao),
            "valor_km_control_sul": str(valor_km_control_sul()),
            "resumo_erros": resumo_erros,
            "clientes_selecionados_ids": clientes_post_ids,
            "clientes_selecionados_nomes": clientes_post_nomes,
            "motivos_clientes_relatorio": motivos_clientes,
            "tecnicos_selecionados_ids": tecnicos_post_ids,
            "tecnicos_selecionados_nomes": tecnicos_post_nomes,
        },
    )


# Atalhos de URL mantidos por compatibilidade de roteamento
def relatorio_create(request):
    return relatorio_form_view(request)


def relatorio_update(request, pk):
    return relatorio_form_view(request, pk=pk)


# ─────────────────────────────────────────────
# LINHA PARCIAL — DESPESA (fetch via JS)
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def nova_linha_despesa(request):
    """
    Retorna o HTML de uma nova linha de despesa vazia.
    Chamada via fetch do JavaScript ao clicar em "Adicionar Despesa".
    """
    idx = request.GET.get("idx", 0)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0

    form = ItemDespesaForm(prefix=f"despesas-{idx}")

    return render(
        request,
        "partials/_linha_despesa.html",
        {
            "form": form,
            "idx": idx,
        },
    )


# ─────────────────────────────────────────────
# LINHA PARCIAL — TRECHO KM (fetch via JS)
# ─────────────────────────────────────────────


@login_required
@exigir_acesso_erp
def nova_linha_km(request):
    """
    Retorna o HTML de uma nova linha de trecho KM.
    Chamada via fetch do JavaScript ao clicar em "Adicionar trecho".

    Recebe via GET:
    - idx             : índice da linha (int)
    - valor_km_padrao : valor R$/km do cliente atual (float como string)
                        É uma variável auxiliar — não é campo do model.
                        Usada apenas para pré-preencher o campo valor_km
                        na linha nova (initial do form).
    """
    # Índice da linha
    try:
        idx = int(request.GET.get("idx", 0))
    except (TypeError, ValueError):
        idx = 0

    # valor_km_padrao: vem como string do JS, pode ser None, "" ou "0"
    # Converte para float com fallback seguro para 0.0
    raw = request.GET.get("valor_km_padrao", "")
    try:
        valor_km_padrao = float(raw) if raw not in (None, "", "None") else 0.0
    except (TypeError, ValueError):
        logger.warning("nova_linha_km: valor_km_padrao inválido recebido: %r", raw)
        valor_km_padrao = 0.0

    logger.debug("nova_linha_km: idx=%s, valor_km_padrao=%s", idx, valor_km_padrao)

    form = TrechoKmForm(
        prefix=f"trechos-{idx}",
        valor_km_padrao=valor_km_padrao,  # repassado ao __init__ via kwargs.pop()
    )

    return render(
        request,
        "partials/_linha_trecho.html",
        {
            "form": form,
            "idx": idx,
        },
    )


# ─────────────────────────────────────────────
# DETALHE
# ─────────────────────────────────────────────


@require_GET
@login_required
@exigir_acesso_erp
def anexo_visualizar_view(request, pk):
    anexo = get_object_or_404(
        AnexoRelatorio.objects.select_related("relatorio"),
        pk=pk,
    )
    _validar_acesso_relatorio_arquivo(request.user, anexo.relatorio, request)
    return _responder_arquivo_anexo(
        anexo.arquivo,
        nome_original=anexo.nome_original,
        tipo_mime=anexo.tipo_mime,
        download=False,
    )


@require_GET
@login_required
@exigir_acesso_erp
def anexo_baixar_view(request, pk):
    anexo = get_object_or_404(
        AnexoRelatorio.objects.select_related("relatorio"),
        pk=pk,
    )
    _validar_acesso_relatorio_arquivo(request.user, anexo.relatorio, request)
    return _responder_arquivo_anexo(
        anexo.arquivo,
        nome_original=anexo.nome_original,
        tipo_mime=anexo.tipo_mime,
        download=True,
    )


@require_GET
@login_required
@exigir_acesso_erp
def despesa_comprovante_visualizar_view(request, pk):
    despesa = get_object_or_404(
        ItemDespesa.objects.select_related("relatorio"),
        pk=pk,
    )
    _validar_acesso_relatorio_arquivo(request.user, despesa.relatorio, request)
    return _responder_arquivo_anexo(despesa.comprovante, download=False)


@require_GET
@login_required
@exigir_acesso_erp
def despesa_comprovante_baixar_view(request, pk):
    despesa = get_object_or_404(
        ItemDespesa.objects.select_related("relatorio"),
        pk=pk,
    )
    _validar_acesso_relatorio_arquivo(request.user, despesa.relatorio, request)
    return _responder_arquivo_anexo(despesa.comprovante, download=True)


@login_required
@exigir_acesso_erp
def relatorio_detail_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente", "tecnico_responsavel"
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "despesas__rateios__cliente",
                "despesas__anexos",
                "trechos__clientes_vinculados__cliente",
                "trechos__rateios__cliente",
                "equipe__tecnico",
                "historicos__usuario",
                "anexos",
            ),
        ),
        pk=pk,
    )
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return redirect("relatorios:relatorio_consulta", pk=relatorio.pk)

    inconsistencias_rateio = []
    try:
        garantir_rateios_relatorio(relatorio)
    except RateioError as exc:
        inconsistencias_rateio = [str(exc)]
    relatorio = (
        RelatorioTecnico.objects.select_related(
            "cliente", "tecnico_responsavel", "aprovado_por", "criado_por", "snapshot_financeiro"
        )
        .prefetch_related(
            "clientes_vinculados__cliente",
            "despesas__clientes_vinculados__cliente",
            "despesas__rateios__cliente",
            "trechos__clientes_vinculados__cliente",
            "trechos__rateios__cliente",
            "equipe__tecnico",
            "historicos__usuario",
        )
        .get(pk=relatorio.pk)
    )
    distribuicao_clientes = resumo_financeiro_por_cliente(relatorio)
    clientes_sem_valor_km_relatorio = (
        clientes_relatorio_sem_valor_km(relatorio)
        if usuario_pode_atuar_como_financeiro(request.user)
        else []
    )

    return render(
        request,
        "relatorios/relatorio_detail.html",
        {
            "relatorio": relatorio,
            "avisos_financeiro": (
                _avisos_financeiro(relatorio)
                if usuario_pode_atuar_como_financeiro(request.user)
                else []
            ),
            "pode_editar_relatorio": _relatorio_editavel_por_usuario(
                relatorio, request.user
            ),
            "pode_atuar_financeiro": usuario_pode_atuar_como_financeiro(request.user),
            "pode_alterar_itens_financeiros": (
                usuario_eh_superadmin(request.user)
                or (
                    usuario_pode_atuar_como_financeiro(request.user)
                    and relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}
                )
            ),
            "superadmin_django": usuario_eh_superadmin(request.user),
            "pode_enviar_relatorio": usuario_pode_enviar_relatorio(request.user, relatorio),
            "inconsistencias_rateio": inconsistencias_rateio,
            "clientes_sem_valor_km_relatorio": clientes_sem_valor_km_relatorio,
            "distribuicao_clientes": distribuicao_clientes,
            "anexos_visualizacao": _anexos_visualizacao_relatorio(relatorio),
            "mapa_trechos_json": _mapa_trechos_relatorio(relatorio),
            "titulo_pagina": f"Relatório {relatorio.identificador}",
        },
    )


@login_required
@exigir_acesso_erp
def relatorio_consulta_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
                "aprovado_por",
                "criado_por",
                "snapshot_financeiro",
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "despesas__rateios__cliente",
                "despesas__anexos",
                "trechos__clientes_vinculados__cliente",
                "trechos__rateios__cliente",
                "equipe__tecnico",
                "historicos__usuario",
                "anexos",
            ),
        ),
        pk=pk,
    )
    if relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        messages.error(
            request,
            "A consulta final fica disponível apenas para relatórios aprovados ou rejeitados.",
        )
        return redirect("relatorios:relatorio_detail", pk=relatorio.pk)
    consulta = montar_consulta_relatorio(relatorio)

    return render(
        request,
        "relatorios/relatorio_consulta.html",
        {
            "relatorio": relatorio,
            "consulta": consulta,
            "distribuicao_clientes": consulta["distribuicao_clientes"],
            "anexos_visualizacao": _anexos_visualizacao_relatorio(relatorio),
            "mapa_trechos_json": consulta.get("mapa_trechos", []),
            "pode_gerar_pdf_interno": usuario_pode_atuar_como_financeiro(request.user),
            "titulo_pagina": f"Consulta {consulta['relatorio'].identificador}",
        },
    )


# ─────────────────────────────────────────────
# EXCLUIR
# ─────────────────────────────────────────────


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_reembolso_pdf_view(request, pk):
    return relatorio_clientes_pdf_view(request, pk)

    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF oficial só pode ser gerado após aprovação.")
        if relatorio.status == StatusRelatorio.REJEITADO:
            return redirect("relatorios:relatorio_consulta", pk=pk)
        return redirect("relatorios:relatorio_detail", pk=pk)

    itens = _itens_pdf_reembolso(relatorio)
    total = sum((item["valor"] for item in itens), Decimal("0.00"))
    emitido_em = timezone.localtime(timezone.now())

    html = render_to_string(
        "pdf/relatorio_reembolso.html",
        {
            "relatorio": relatorio,
            "itens": itens,
            "total": total,
            "emitido_em": emitido_em,
            "empresa": "CONTROLSUL GESTÃO EMPRESARIAL",
        },
        request=request,
    )

    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        logger.exception("WeasyPrint não está disponível: %s", exc)
        messages.error(
            request,
            "WeasyPrint não está disponível neste ambiente. Verifique a instalação das dependências nativas.",
        )
        return redirect("relatorios:relatorio_consulta", pk=pk)

    css_path = settings.BASE_DIR / "templates" / "pdf" / "relatorio_reembolso.css"
    pdf = HTML(
        string=html,
        encoding="utf-8",
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-reembolso-{relatorio.numero}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def _relatorio_pdf_cliente_or_404(request, pk):
    return get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
                "snapshot_financeiro",
            ).prefetch_related(
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "despesas__rateios__cliente",
                "trechos__clientes_vinculados__cliente",
                "trechos__rateios__cliente",
                "equipe__tecnico",
            ),
        ),
        pk=pk,
    )

def _redirect_pdf_cliente_error(relatorio):
    if relatorio.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        return redirect("relatorios:relatorio_consulta", pk=relatorio.pk)
    return redirect("relatorios:relatorio_detail", pk=relatorio.pk)


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_cliente_pdf_view(request, pk, cliente_id):
    relatorio = _relatorio_pdf_cliente_or_404(request, pk)
    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF do cliente só pode ser gerado após aprovação.")
        return _redirect_pdf_cliente_error(relatorio)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do PDF de cliente para relatorio %s cliente %s.", pk, cliente_id)
    try:
        pdf, contexto = gerar_pdf_cliente(relatorio, cliente_id, request=request)
    except PermissionDenied:
        raise
    except PdfClienteError as exc:
        logger.exception(
            "Erro ao gerar PDF do cliente %s no relatorio %s: %s",
            cliente_id,
            relatorio.pk,
            exc,
        )
        messages.error(request, str(exc))
        return _redirect_pdf_cliente_error(relatorio)

    filename = nome_arquivo_pdf_cliente(relatorio, contexto["cliente"])
    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 5:
        logger.warning("PDF de cliente lento. relatorio=%s cliente=%s duracao=%.2fs", pk, cliente_id, duracao_pdf)
    logger.info("PDF de cliente gerado para relatorio %s cliente %s.", pk, cliente_id)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_clientes_pdf_view(request, pk):
    relatorio = _relatorio_pdf_cliente_or_404(request, pk)
    if relatorio.status != StatusRelatorio.APROVADO:
        messages.error(request, "O PDF do cliente só pode ser gerado após aprovação.")
        return _redirect_pdf_cliente_error(relatorio)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do ZIP de PDFs de clientes para relatorio %s.", pk)
    try:
        zip_bytes, gerados, ignorados = gerar_zip_pdfs_clientes(
            relatorio,
            request=request,
        )
    except PermissionDenied:
        raise
    except PdfClienteError as exc:
        logger.exception(
            "Erro ao gerar PDFs dos clientes do relatorio %s: %s",
            relatorio.pk,
            exc,
        )
        messages.error(request, str(exc))
        return _redirect_pdf_cliente_error(relatorio)

    if ignorados:
        logger.info(
            "PDFs de clientes gerados para relatorio %s com %s arquivo(s) e %s cliente(s) ignorado(s).",
            relatorio.pk,
            len(gerados),
            len(ignorados),
        )

    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 8:
        logger.warning("ZIP de PDFs de clientes lento. relatorio=%s duracao=%.2fs", pk, duracao_pdf)
    logger.info("ZIP de PDFs de clientes gerado para relatorio %s com %s arquivo(s).", pk, len(gerados))
    filename = f"relatorio_{relatorio.numero or relatorio.pk}_clientes.zip"
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
@login_required
@exigir_financeiro
def relatorio_pdf_interno_view(request, pk):
    relatorio = get_object_or_404(
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
            "aprovado_por",
            "snapshot_financeiro",
        ).prefetch_related(
            "clientes_vinculados__cliente",
            "despesas__clientes_vinculados__cliente",
            "despesas__rateios__cliente",
            "trechos__clientes_vinculados__cliente",
            "trechos__rateios__cliente",
            "equipe__tecnico",
            "historicos__usuario",
        ),
        pk=pk,
    )
    if relatorio.status not in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
        messages.error(
            request,
            "O PDF interno só pode ser gerado para relatórios finalizados.",
        )
        return redirect("relatorios:relatorio_detail", pk=pk)

    inicio_pdf = time.perf_counter()
    logger.info("Inicio da geracao do PDF interno do relatorio %s.", pk)
    emitido_em = timezone.localtime(timezone.now())
    usuario_gerador = request.user if request.user.is_authenticated else None
    pdf_contexto = montar_contexto_pdf_interno(
        relatorio,
        emitido_em,
        usuario_gerador=usuario_gerador,
        avisos_financeiro=_avisos_financeiro(relatorio),
    )

    html = render_to_string(
        "relatorios/pdf/interno.html",
        {
            "pdf": pdf_contexto,
            "empresa": "CONTROLSUL GESTÃO EMPRESARIAL",
            "emitido_em": emitido_em,
            "usuario_gerador": usuario_gerador,
        },
        request=request,
    )

    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        logger.exception("WeasyPrint não está disponível: %s", exc)
        messages.error(
            request,
            "WeasyPrint não está disponível neste ambiente. Verifique a instalação das dependências nativas.",
        )
        return redirect("relatorios:relatorio_consulta", pk=pk)

    css_path = settings.BASE_DIR / "static" / "css" / "pdf-relatorio.css"
    pdf = HTML(
        string=html,
        encoding="utf-8",
        base_url=request.build_absolute_uri("/"),
    ).write_pdf(stylesheets=[CSS(filename=str(css_path))])

    filename = f"relatorio-interno-{relatorio.numero}.pdf"
    duracao_pdf = time.perf_counter() - inicio_pdf
    if duracao_pdf > 5:
        logger.warning("PDF interno lento. relatorio=%s duracao=%.2fs", pk, duracao_pdf)
    logger.info("PDF interno gerado para relatorio %s.", pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


# ─────────────────────────────────────────────
# MUDAR STATUS
# ─────────────────────────────────────────────


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_import_list_json(request):
    qs = _relatorios_visiveis(
        request.user,
        RelatorioTecnico.objects.select_related(
            "cliente",
            "tecnico_responsavel",
        ),
    ).order_by("-data_inicio", "-criado_em")

    cliente_id = request.GET.get("cliente")
    tecnico_id = request.GET.get("tecnico")
    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")
    busca = (request.GET.get("busca") or "").strip()
    excluir = request.GET.get("excluir")

    if cliente_id:
        qs = qs.filter(cliente_id=cliente_id)
    if tecnico_id:
        qs = qs.filter(tecnico_responsavel_id=tecnico_id)
    if data_inicio:
        qs = qs.filter(data_inicio__gte=data_inicio)
    if data_fim:
        qs = qs.filter(data_fim__lte=data_fim)
    if busca:
        busca_digits = re.sub(r"\D+", "", busca)
        qs = qs.filter(
            Q(numero__icontains=busca)
            | Q(cliente__nome__icontains=busca)
            | Q(cliente__razao_social__icontains=busca)
            | Q(cliente__nome_fantasia__icontains=busca)
            | Q(cliente__cnpj_cpf__icontains=busca_digits or busca)
            | Q(tecnico_responsavel__nome__icontains=busca)
            | Q(cidade_atendimento__icontains=busca)
            | Q(motivo__icontains=busca)
        )
    if excluir:
        qs = qs.exclude(pk=excluir)

    relatorios = [
        {
            "id": relatorio.pk,
            "numero": relatorio.identificador,
            "data": _formatar_data(relatorio.data_inicio),
            "cliente": _nome_cliente(relatorio.cliente),
            "tecnico": relatorio.tecnico_responsavel.nome,
            "status": relatorio.get_status_display(),
            "total": _formatar_moeda(relatorio.total_despesas),
        }
        for relatorio in qs[:30]
    ]
    return JsonResponse({"relatorios": relatorios})


@require_GET
@login_required
@exigir_acesso_erp
def relatorio_import_detail_json(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
            ).prefetch_related(
                "equipe__tecnico",
                "clientes_vinculados__cliente",
                "despesas__clientes_vinculados__cliente",
                "trechos__clientes_vinculados__cliente",
            ),
        ),
        pk=pk,
    )
    clientes_relatorio = obter_clientes_relatorio(relatorio)
    motivos_clientes = obter_motivos_clientes_relatorio(relatorio)

    return JsonResponse(
        {
            "id": relatorio.pk,
            "numero": relatorio.identificador,
            "cliente_id": relatorio.cliente_id,
            "cliente_ids": [cliente.pk for cliente in clientes_relatorio],
            "motivos_clientes": motivos_clientes,
            "tecnico_id": relatorio.tecnico_responsavel_id,
            "apoio_ids": list(relatorio.equipe.values_list("tecnico_id", flat=True)),
            "valor_adiantamento": str(relatorio.valor_adiantamento or Decimal("0.00")),
            "km_excedente_interno": str(relatorio.km_excedente_interno or Decimal("0.00")),
            "observacao_km_excedente": relatorio.observacao_km_excedente or "",
            "despesas": [
                {
                    "tipo": despesa.tipo,
                    "descricao": despesa.descricao,
                    "valor": str(despesa.valor),
                    "observacoes": despesa.observacoes,
                    "cliente_ids": list(
                        despesa.clientes_vinculados.values_list("cliente_id", flat=True)
                    ),
                }
                for despesa in relatorio.despesas.all()
            ],
            "trechos": [
                {
                    "origem": trecho.origem,
                    "origem_endereco_completo": trecho.origem_endereco_completo,
                    "origem_lat": str(trecho.origem_lat or ""),
                    "origem_lon": str(trecho.origem_lon or ""),
                    "destino": trecho.destino,
                    "destino_endereco_completo": trecho.destino_endereco_completo,
                    "destino_lat": str(trecho.destino_lat or ""),
                    "destino_lon": str(trecho.destino_lon or ""),
                    "km": str(trecho.km),
                    "km_calculado_api": str(trecho.km_calculado_api or ""),
                    "km_informado": str(trecho.km_informado or trecho.km or ""),
                    "diferenca_km_percentual": str(trecho.diferenca_km_percentual or ""),
                    "fonte_calculo_rota": trecho.fonte_calculo_rota or "",
                    "rota_geojson": trecho.rota_geojson or {},
                    "valor_km": str(trecho.valor_km),
                    "cliente_ids": list(
                        trecho.clientes_vinculados.values_list("cliente_id", flat=True)
                    ),
                }
                for trecho in relatorio.trechos.all()
            ],
        }
    )


@login_required
@require_GET
@exigir_acesso_erp
def mapa_buscar_endereco_json(request):
    query = (request.GET.get("q") or "").strip()
    if not query:
        return JsonResponse(
            {"success": False, "error": "Informe um endereço para buscar."},
            status=400,
        )

    inicio = time.perf_counter()
    try:
        resultados = buscar_endereco(query)
    except MapsServiceError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Erro inesperado ao buscar endereço: %s", exc)
        return JsonResponse(
            {"success": False, "error": "Erro interno ao buscar endereço."},
            status=500,
        )

    duracao = time.perf_counter() - inicio
    if duracao > 3:
        logger.warning("Busca de endereco lenta. duracao=%.2fs tamanho_query=%s", duracao, len(query))
    return JsonResponse({"success": True, "data": resultados})


@login_required
@require_GET
@exigir_acesso_erp
def mapa_calcular_rota_json(request):
    campos = ("origem_lat", "origem_lon", "destino_lat", "destino_lon")
    parametros = {campo: request.GET.get(campo) for campo in campos}
    faltando = [
        campo
        for campo, valor in parametros.items()
        if not str(valor or "").strip()
    ]
    if faltando:
        return JsonResponse(
            {
                "success": False,
                "error": "Informe origem e destino completos para calcular a rota.",
            },
            status=400,
        )

    inicio = time.perf_counter()
    try:
        rota = calcular_rota(**parametros)
    except MapsServiceError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Erro inesperado ao calcular rota: %s", exc)
        return JsonResponse(
            {"success": False, "error": "Erro interno ao calcular rota."},
            status=500,
        )

    duracao = time.perf_counter() - inicio
    if duracao > 3:
        logger.warning("Calculo de rota lento. duracao=%.2fs", duracao)
    return JsonResponse({"success": True, "data": rota})


@require_POST
@login_required
@exigir_acesso_erp
def relatorio_duplicate_view(request, pk):
    original = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related(
                "cliente",
                "tecnico_responsavel",
            ).prefetch_related(
                "equipe__tecnico",
                "despesas",
                "trechos",
            ),
        ),
        pk=pk,
    )

    if original.status == StatusRelatorio.AJUSTE:
        messages.warning(
            request,
            "Relatorio devolvido para ajuste deve ser corrigido no proprio registro.",
        )
        return redirect("relatorios:relatorio_update", pk=original.pk)

    try:
        with transaction.atomic():
            usuario_historico = request.user if request.user.is_authenticated else None
            novo = _duplicar_relatorio(original, usuario_historico)
    except Exception as exc:
        logger.exception("Erro ao duplicar relatório %s: %s", pk, exc)
        messages.error(request, "Erro interno ao duplicar relatório. Tente novamente.")
        return redirect("relatorios:relatorio_list")

    messages.success(
        request,
        f"Relatório {original.identificador} duplicado como {novo.identificador}.",
    )
    return redirect("relatorios:relatorio_update", pk=novo.pk)


def _usuario_pode_excluir_rascunho(user, relatorio):
    if relatorio.status != StatusRelatorio.RASCUNHO:
        return False
    if usuario_eh_administrativo(user) or usuario_eh_superadmin(user):
        return True
    return usuario_pode_editar_relatorio(user, relatorio)


@require_POST
@login_required
@exigir_acesso_erp
def relatorio_excluir_rascunho_view(request, pk):
    relatorio = get_object_or_404(
        _relatorios_visiveis(
            request.user,
            RelatorioTecnico.objects.select_related("cliente", "tecnico_responsavel", "criado_por"),
        ),
        pk=pk,
    )
    if relatorio.status != StatusRelatorio.RASCUNHO:
        rascunhos_logger.warning(
            "exclusao_rascunho_bloqueada_status usuario=%s relatorio=%s status=%s",
            getattr(request.user, "pk", None),
            relatorio.pk,
            relatorio.status,
        )
        messages.error(
            request,
            "Este relatório não pode ser excluído porque já foi enviado para conferência.",
        )
        return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

    if not _usuario_pode_excluir_rascunho(request.user, relatorio):
        rascunhos_logger.warning(
            "exclusao_rascunho_bloqueada_permissao usuario=%s relatorio=%s",
            getattr(request.user, "pk", None),
            relatorio.pk,
        )
        raise PermissionDenied("Você não tem permissão para excluir este rascunho.")

    identificador = relatorio.identificador
    relatorio_id = relatorio.pk
    with transaction.atomic():
        rascunhos_logger.info(
            "exclusao_rascunho usuario=%s relatorio=%s identificador=%s status=%s",
            getattr(request.user, "pk", None),
            relatorio_id,
            identificador,
            relatorio.status,
        )
        relatorio.delete()

    messages.success(request, "Rascunho excluído com sucesso.")
    return redirect("relatorios:relatorio_list")


@require_POST
@login_required
@exigir_financeiro
def relatorio_item_financeiro_view(request, pk, tipo, item_pk, acao):
    espera_json = _request_espera_json(request)
    def resposta_erro(mensagem, status=400):
        if espera_json:
            return JsonResponse({"success": False, "errors": [mensagem]}, status=status)
        messages.error(request, mensagem)
        return redirect("relatorios:relatorio_detail", pk=pk)

    if tipo not in {"despesa", "trecho"} or acao not in {"rejeitar", "restaurar"}:
        return resposta_erro("Ação inválida para o item.")

    try:
        with transaction.atomic():
            relatorio = get_object_or_404(
                RelatorioTecnico.objects.select_for_update(), pk=pk
            )
            if _relatorio_bloqueado(relatorio):
                return resposta_erro(
                    "Relatório aprovado ou rejeitado está bloqueado para alterações.",
                )

            modelo = ItemDespesa if tipo == "despesa" else TrechoKm
            item = get_object_or_404(
                modelo.objects.select_for_update(),
                pk=item_pk,
                relatorio=relatorio,
            )

            usuario_historico = (
                request.user if request.user.is_authenticated else None
            )

            if acao == "rejeitar":
                motivo = (
                    request.POST.get("motivo_rejeicao")
                    or request.POST.get("motivo_recusa")
                    or ""
                ).strip()
                if not motivo:
                    return resposta_erro("Informe a justificativa da rejeição do item.")

                agora = timezone.now()
                item.rejeitado = True
                item.motivo_rejeicao = motivo
                item.rejeitado_por = usuario_historico
                item.rejeitado_em = agora
                item.status_financeiro = StatusFinanceiroItem.REJEITADO
                item.motivo_recusa = motivo
                item.save(
                    update_fields=[
                        "rejeitado",
                        "motivo_rejeicao",
                        "rejeitado_por",
                        "rejeitado_em",
                        "status_financeiro",
                        "motivo_recusa",
                    ]
                )
                if tipo == "despesa":
                    garantir_rateio_despesa(item)
                else:
                    garantir_rateio_trecho(item)

                if tipo == "despesa":
                    descricao = f"Despesa rejeitada pelo financeiro: {motivo}"
                else:
                    descricao = f"Trecho KM rejeitado pelo financeiro: {motivo}"

                registrar_evento(
                    relatorio,
                    usuario_historico,
                    TipoEventoHistorico.ITEM_REJEITADO,
                    descricao,
                    {
                        "tipo_item": tipo,
                        "item_id": item.pk,
                        "motivo": motivo,
                    },
                )
                logger.info(
                    "Item financeiro rejeitado. relatorio=%s tipo=%s item=%s usuario=%s",
                    relatorio.pk,
                    tipo,
                    item.pk,
                    getattr(request.user, "pk", None),
                )
                mensagem_sucesso = "Item removido do reembolso."

            else:
                item.rejeitado = False
                item.motivo_rejeicao = ""
                item.rejeitado_por = None
                item.rejeitado_em = None
                item.status_financeiro = StatusFinanceiroItem.APROVADO
                item.motivo_recusa = ""
                item.save(
                    update_fields=[
                        "rejeitado",
                        "motivo_rejeicao",
                        "rejeitado_por",
                        "rejeitado_em",
                        "status_financeiro",
                        "motivo_recusa",
                    ]
                )
                if tipo == "despesa":
                    garantir_rateio_despesa(item)
                else:
                    garantir_rateio_trecho(item)
                registrar_evento(
                    relatorio,
                    usuario_historico,
                    TipoEventoHistorico.ITEM_REATIVADO,
                    "Item restaurado pelo financeiro.",
                    {
                        "tipo_item": tipo,
                        "item_id": item.pk,
                    },
                )
                logger.info(
                    "Item financeiro reativado. relatorio=%s tipo=%s item=%s usuario=%s",
                    relatorio.pk,
                    tipo,
                    item.pk,
                    getattr(request.user, "pk", None),
                )
                mensagem_sucesso = "Item restaurado para o reembolso."

        if espera_json:
            payload_financeiro = montar_payload_financeiro_por_id(pk)
            return JsonResponse(
                {
                    "success": True,
                    "tipo": tipo,
                    "item_id": item.pk,
                    "acao": acao,
                    "rejeitado": bool(item.rejeitado),
                    "status_financeiro": item.status_financeiro,
                    "valor_final": str(
                        item.valor_final if tipo == "despesa" else item.valor_final_clientes
                    ),
                    "valor_reembolso_tecnico": (
                        str(item.valor_reembolso_tecnico) if tipo == "trecho" else ""
                    ),
                    "valor_cobranca_cliente": (
                        str(item.valor_final_clientes) if tipo == "trecho" else ""
                    ),
                    "message": mensagem_sucesso,
                    "financeiro": payload_financeiro,
                }
            )
        messages.success(request, mensagem_sucesso)

    except Exception as exc:
        logger.exception("Erro ao alterar item financeiro do relatório %s: %s", pk, exc)
        if espera_json:
            return JsonResponse(
                {
                    "success": False,
                    "errors": ["Erro interno ao alterar item. Tente novamente."],
                },
                status=500,
            )
        messages.error(request, "Erro interno ao alterar item. Tente novamente.")

    return redirect("relatorios:relatorio_detail", pk=pk)


@require_POST
@login_required
@exigir_financeiro
def relatorio_rateio_financeiro_json(request, pk, tipo, item_pk):
    if tipo not in {"despesa", "trecho"}:
        return JsonResponse({"success": False, "errors": ["Tipo de item inválido."]}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "errors": ["JSON inválido."]}, status=400)

    try:
        with transaction.atomic():
            relatorio = get_object_or_404(
                _relatorios_visiveis(
                    request.user,
                    RelatorioTecnico.objects.select_for_update(),
                ),
                pk=pk,
            )
            if _relatorio_bloqueado(relatorio):
                return JsonResponse(
                    {"success": False, "errors": ["Relatorio aprovado ou rejeitado esta bloqueado."]},
                    status=400,
                )

            modelo = ItemDespesa if tipo == "despesa" else TrechoKm
            item = get_object_or_404(
                modelo.objects.select_for_update(),
                pk=item_pk,
                relatorio=relatorio,
            )
            if item.rejeitado or item.status_financeiro == StatusFinanceiroItem.REJEITADO:
                return JsonResponse(
                    {"success": False, "errors": ["Item rejeitado nao pode ter rateio alterado."]},
                    status=400,
                )

            acao = payload.get("acao") or "salvar"
            aprovar = acao == "aprovar"
            dados_rateio = payload.get("rateios") or []
            motivo = payload.get("motivo") or ""

            if tipo == "despesa":
                garantir_rateio_despesa(item)
                rateios = salvar_rateio_despesa(
                    item,
                    dados_rateio,
                    request.user,
                    motivo=motivo,
                    aprovar=aprovar,
                )
            else:
                garantir_rateio_trecho(item)
                rateios = salvar_rateio_trecho(
                    item,
                    dados_rateio,
                    request.user,
                    motivo=motivo,
                    aprovar=aprovar,
                )

        return JsonResponse(
            {
                "success": True,
                "rateios": [serializar_rateio(rateio) for rateio in rateios],
                "message": "Rateio salvo com sucesso.",
                "financeiro": montar_payload_financeiro_por_id(pk),
            }
        )
    except RateioError as exc:
        return JsonResponse({"success": False, "errors": [str(exc)]}, status=400)
    except Exception as exc:
        logger.exception("Erro ao salvar rateio do relatório %s: %s", pk, exc)
        return JsonResponse(
            {"success": False, "errors": ["Erro interno ao salvar rateio."]},
            status=500,
        )


@require_POST
@login_required
@exigir_acesso_erp
def relatorio_status_view(request, pk, status):
    try:
        usuario_historico = request.user if request.user.is_authenticated else None
        relatorio_atual = get_object_or_404(
            _relatorios_visiveis(request.user, RelatorioTecnico.objects.all()),
            pk=pk,
        )
        if status == StatusRelatorio.CONFERENCIA:
            _log_envio_relatorio_pre_autorizacao(
                request,
                relatorio_atual,
                "relatorio_status_view",
            )
            if not usuario_pode_enviar_relatorio(request.user, relatorio_atual):
                _registrar_bloqueio_seguranca(
                    request,
                    "Envio de relatorio bloqueado",
                    relatorio_id=pk,
                    status=status,
                )
                messages.error(request, "Você não tem permissão para enviar este relatório.")
                return redirect("relatorios:relatorio_detail", pk=pk)
            relatorio = enviar_para_conferencia(pk, usuario_historico)
        elif status in {
            StatusRelatorio.AJUSTE,
            StatusRelatorio.REJEITADO,
            StatusRelatorio.APROVADO,
        } and not usuario_pode_atuar_como_financeiro(request.user):
            _registrar_bloqueio_seguranca(
                request,
                "Acao financeira de workflow bloqueada",
                relatorio_id=pk,
                status=status,
            )
            messages.error(request, "Você não tem permissão para executar esta ação financeira.")
            return redirect("relatorios:relatorio_detail", pk=pk)
        elif status == StatusRelatorio.AJUSTE:
            relatorio = solicitar_ajuste(
                pk,
                usuario_historico,
                request.POST.get("motivo_rejeicao", ""),
            )
        elif status == StatusRelatorio.REJEITADO:
            relatorio = rejeitar_relatorio(
                pk,
                usuario_historico,
                request.POST.get("motivo_rejeicao", ""),
            )
        elif status == StatusRelatorio.APROVADO:
            relatorio = aprovar_relatorio(pk, usuario_historico, request.POST)
        else:
            messages.error(request, "Status inválido.")
            return redirect("relatorios:relatorio_detail", pk=pk)
    except WorkflowError as exc:
        erros = _lista_erros_operacionais(exc)
        if _request_espera_json(request):
            return JsonResponse({"success": False, "errors": erros}, status=400)
        _adicionar_erros_operacionais(request, erros)
        return redirect("relatorios:relatorio_detail", pk=pk)
    except RelatorioTecnico.DoesNotExist:
        messages.error(request, "Relatório não encontrado.")
        return redirect("relatorios:relatorio_list")
    except Exception as exc:
        logger.exception("Erro ao alterar status do relatório %s: %s", pk, exc)
        messages.error(request, "Erro interno ao alterar status. Tente novamente.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    messages.success(
        request,
        f'Status alterado para "{relatorio.get_status_display()}".',
    )
    if getattr(relatorio, "_email_warning", ""):
        messages.warning(request, relatorio._email_warning)
    return redirect("relatorios:relatorio_detail", pk=pk)


@require_POST
@login_required
@exigir_financeiro
def relatorio_valores_financeiros_json(request, pk):
    try:
        with transaction.atomic():
            relatorio = get_object_or_404(
                RelatorioTecnico.objects.select_for_update(),
                pk=pk,
            )
            if _relatorio_bloqueado(relatorio):
                return JsonResponse(
                    {
                        "success": False,
                        "errors": ["Relatorio aprovado ou rejeitado esta bloqueado."],
                    },
                    status=400,
                )
            _salvar_valores_aprovados(request.POST, relatorio, request.user, consolidar=False)
            garantir_rateios_relatorio(relatorio)
            logger.info(
                "Valores financeiros atualizados. relatorio=%s usuario=%s",
                relatorio.pk,
                getattr(request.user, "pk", None),
            )

        return JsonResponse(
            {
                "success": True,
                "message": "Valores financeiros atualizados.",
                "financeiro": montar_payload_financeiro_por_id(pk),
            }
        )
    except (WorkflowError, RateioError) as exc:
        detalhe = exc.args[0] if exc.args else str(exc)
        erros = detalhe if isinstance(detalhe, list) else [str(detalhe)]
        logger.warning(
            "Atualizacao financeira bloqueada. relatorio=%s usuario=%s erros=%s",
            pk,
            getattr(request.user, "pk", None),
            erros,
        )
        return JsonResponse({"success": False, "errors": erros}, status=400)
    except Exception as exc:
        logger.exception("Erro ao salvar valores financeiros do relatorio %s: %s", pk, exc)
        return JsonResponse(
            {"success": False, "errors": ["Erro interno ao salvar valores financeiros."]},
            status=500,
        )


# ─────────────────────────────────────────────
# TÉCNICOS
# ─────────────────────────────────────────────


class TecnicoListView(AdministrativoMixin, ListView):
    model = Tecnico
    template_name = "tecnicos/tecnico_list.html"
    context_object_name = "tecnicos"
    paginate_by = 20

    def get_queryset(self):
        qs = Tecnico.objects.select_related("setor").all()
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(email__icontains=busca)
                | Q(ad_username__icontains=busca)
                | Q(ad_user_principal_name__icontains=busca)
                | Q(setor__nome__icontains=busca)
                | Q(funcao_setor__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Técnicos"
        ctx["busca"] = self.request.GET.get("busca", "")
        ctx["tecnicos_total_filtrado"] = ctx["paginator"].count if ctx.get("paginator") else len(ctx.get("tecnicos", []))
        ctx["tecnicos_qtd_pagina"] = len(ctx.get("tecnicos", []))
        return ctx


@login_required
@exigir_administrativo
def tecnico_detail_view(request, pk):
    tecnico = get_object_or_404(Tecnico.objects.select_related("setor"), pk=pk)
    usuario = None
    if tecnico.email:
        from django.contrib.auth import get_user_model

        usuario = (
            get_user_model().objects.filter(username__iexact=tecnico.ad_username).first()
            or get_user_model().objects.filter(email__iexact=tecnico.email).first()
        )
    tipo_usuario = "Administrador" if usuario and usuario_eh_administrativo(usuario) else "Usuário"
    return render(
        request,
        "tecnicos/tecnico_detail.html",
        {
            "tecnico": tecnico,
            "usuario_vinculado": usuario,
            "tipo_usuario": tipo_usuario,
            "titulo_pagina": "Detalhe do técnico",
        },
    )


@login_required
@exigir_administrativo
def tecnico_form_view(request, pk=None):
    if pk:
        messages.info(request, "Os dados de técnicos são sincronizados pelo AD. Esta tela é somente leitura.")
        return redirect("relatorios:tecnico_detail", pk=pk)
    messages.warning(request, "O cadastro de técnicos é realizado automaticamente a partir do AD.")
    return redirect("relatorios:tecnico_list")


@login_required
@exigir_administrativo
def tecnico_delete_view(request, pk):
    messages.warning(request, "Técnicos não são removidos manualmente; o status deve vir da sincronização do AD.")
    return redirect("relatorios:tecnico_detail", pk=pk)


# ─────────────────────────────────────────────
# CLIENTES
# ─────────────────────────────────────────────


class ClienteListView(AdministrativoMixin, ListView):
    model = Cliente
    template_name = "clientes/cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 20

    def get_queryset(self):
        qs = Cliente.objects.all().order_by("nome_fantasia", "razao_social", "nome")
        busca = self.request.GET.get("busca", "").strip()
        valor_km = self.request.GET.get("valor_km", "").strip()
        if valor_km == "pendente":
            qs = qs.filter(ativo=True).filter(Q(valor_km__isnull=True) | Q(valor_km__lte=0))
        if busca:
            busca_digits = re.sub(r"\D+", "", busca)
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(razao_social__icontains=busca)
                | Q(nome_fantasia__icontains=busca)
                | Q(cnpj_cpf__icontains=busca)
                | Q(cnpj_cpf__icontains=busca_digits)
                | Q(cidade__icontains=busca)
                | Q(uf__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Clientes"
        ctx["busca"] = self.request.GET.get("busca", "")
        ctx["valor_km_filtro"] = self.request.GET.get("valor_km", "")
        ctx["clientes_total_filtrado"] = ctx["paginator"].count if ctx.get("paginator") else len(ctx.get("clientes", []))
        ctx["clientes_qtd_pagina"] = len(ctx.get("clientes", []))
        return ctx


@login_required
@exigir_administrativo
def cliente_form_view(request, pk=None):
    instance = get_object_or_404(Cliente, pk=pk) if pk else None
    form = ClienteForm(request.POST or None, instance=instance)
    if form.is_valid():
        c = form.save()
        messages.success(request, f"Cliente {c.nome} salvo!")
        return redirect("relatorios:cliente_list")
    return render(
        request,
        "clientes/cliente_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Cliente" if instance else "Novo Cliente",
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar" if instance else "Cadastrar",
        },
    )


@login_required
@exigir_administrativo
def cliente_delete_view(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Cliente removido.")
        return redirect("relatorios:cliente_list")
    return render(
        request,
        "clientes/cliente_confirm_delete.html",
        {
            "object": cliente,
            "titulo_pagina": "Excluir Cliente",
        },
    )


# ─────────────────────────────────────────────
# ADIANTAMENTOS
# ─────────────────────────────────────────────


class AdiantamentoListView(AdministrativoMixin, ListView):
    model = Adiantamento
    template_name = "adiantamentos/adiantamento_list.html"
    context_object_name = "adiantamentos"
    paginate_by = 20

    def get_queryset(self):
        qs = Adiantamento.objects.select_related("tecnico", "relatorio")
        tecnico = self.request.GET.get("tecnico")
        if tecnico:
            qs = qs.filter(tecnico_id=tecnico)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Adiantamentos"
        ctx["tecnicos"] = Tecnico.objects.filter(ativo=True)
        ctx["tecnico_sel"] = self.request.GET.get("tecnico", "")
        ctx["total_geral"] = self.get_queryset().aggregate(t=Sum("valor"))[
            "t"
        ] or Decimal("0.00")
        return ctx


@login_required
@exigir_administrativo
def adiantamento_form_view(request, pk=None):
    instance = get_object_or_404(Adiantamento, pk=pk) if pk else None
    form = AdiantamentoForm(request.POST or None, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, "Adiantamento salvo!")
        return redirect("relatorios:adiantamento_list")
    return render(
        request,
        "adiantamentos/adiantamento_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Adiantamento" if instance else "Novo Adiantamento",
            "salvar_rascunho": "Salvar rascunho",
            "enviar": "Salvar" if instance else "Registrar",
        },
    )


@login_required
@exigir_administrativo
def adiantamento_delete_view(request, pk):
    obj = get_object_or_404(Adiantamento, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Adiantamento removido.")
        return redirect("relatorios:adiantamento_list")
    return render(
        request,
        "adiantamentos/adiantamento_confirm_delete.html",
        {
            "object": obj,
            "titulo_pagina": "Excluir Adiantamento",
        },
    )
