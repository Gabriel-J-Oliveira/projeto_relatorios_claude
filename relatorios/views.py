from django.shortcuts import render, get_object_or_404, redirect, render
from django.views.generic import TemplateView, ListView
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST
from decimal import Decimal
import datetime
import logging

from .models import (
    RelatorioTecnico,
    ItemDespesa,
    TrechoKm,
    Tecnico,
    Cliente,
    Adiantamento,
    StatusRelatorio,
    PoliticaValor,
)
from .forms import (
    RelatorioTecnicoForm,
    ItemDespesaFormSet,
    TrechoKmFormSet,
    TecnicoForm,
    ClienteForm,
    AdiantamentoForm,
    RelatorioFiltroForm,
    ItemDespesaForm,
    TrechoKmForm,
)

# ─────────────────────────────────────────────
# Logs e debug
# ─────────────────────────────────────────────
logger = logging.getLogger(__name__)


def relatorio_form(request, pk=None):
    logger.debug("Entrou na view")

    if request.method == "POST":
        form = RelatorioTecnicoForm(request.POST, request.FILES)
        fs_desp = ItemDespesaFormSet(request.POST, request.FILES)
        fs_km = TrechoKmFormSet(request.POST, request.FILES)

        if form.is_valid() and fs_desp.is_valid() and fs_km.is_valid():
            ...
        else:
            logger.error(form.errors)
            logger.error(fs_desp.errors)
            logger.error(fs_km.errors)


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────


class DashboardView(TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        total = RelatorioTecnico.objects.count()
        d = total or 1

        ctx.update(
            {
                "titulo_pagina": "Dashboard",
                "total_relatorios": total,
                "total_pendentes": RelatorioTecnico.objects.filter(
                    status=StatusRelatorio.PENDENTE
                ).count(),
                "total_tecnicos": Tecnico.objects.filter(ativo=True).count(),
                "total_clientes": Cliente.objects.filter(ativo=True).count(),
                "pct_rascunho": round(
                    RelatorioTecnico.objects.filter(status="rascunho").count() / d * 100
                ),
                "pct_pendente": round(
                    RelatorioTecnico.objects.filter(status="pendente").count() / d * 100
                ),
                "pct_aprovado": round(
                    RelatorioTecnico.objects.filter(status="aprovado").count() / d * 100
                ),
                "pct_faturado": round(
                    RelatorioTecnico.objects.filter(status="faturado").count() / d * 100
                ),
                "relatorios_recentes": RelatorioTecnico.objects.select_related(
                    "cliente", "tecnico_responsavel"
                ).order_by("-criado_em")[:8],
            }
        )
        return ctx


# ─────────────────────────────────────────────
# LISTAGEM
# ─────────────────────────────────────────────


class RelatorioListView(ListView):
    model = RelatorioTecnico
    template_name = "relatorios/relatorio_list.html"
    context_object_name = "relatorios"
    paginate_by = 15

    def get_queryset(self):
        qs = RelatorioTecnico.objects.select_related("cliente", "tecnico_responsavel")
        form = RelatorioFiltroForm(self.request.GET)
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
                qs = qs.filter(
                    Q(numero__icontains=q)
                    | Q(cliente__nome__icontains=q)
                    | Q(tecnico_responsavel__nome__icontains=q)
                    | Q(cidade_atendimento__icontains=q)
                )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_filtro"] = RelatorioFiltroForm(self.request.GET)
        ctx["titulo_pagina"] = "Relatórios Técnicos"
        ctx["total"] = self.get_queryset().count()
        return ctx


# ─────────────────────────────────────────────
# CRIAR / EDITAR  — Single Save
# ─────────────────────────────────────────────
def relatorio_form_view(request, pk=None):
    """
    View unificada: cria ou edita relatório.
    """
    instance = get_object_or_404(RelatorioTecnico, pk=pk) if pk else None

    if request.method == "POST":
        # FORM PRINCIPAL
        form = RelatorioTecnicoForm(
            request.POST,
            request.FILES,
            instance=instance,
        )

        # FORMSET DESPESAS
        fs_desp = ItemDespesaFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="despesas",
        )

        # FORMSET KM
        fs_km = TrechoKmFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="trechos",
        )

        # Validação individual
        form_ok = form.is_valid()
        fs_desp_ok = fs_desp.is_valid()
        print("\n========== POST TRECHOS ==========")
        for k, v in request.POST.items():
            if "trechos" in k:
                print(f"{k}: {v}")
        print("==================================\n")
        fs_km_ok = fs_km.is_valid()

        # DEBUG NO TERMINAL
        print("\n========== DEBUG VALIDAÇÃO ==========")
        print("FORM OK:", form_ok)
        print("FORM ERRORS:", form.errors)

        print("\nDESP OK:", fs_desp_ok)
        print("DESP ERRORS:", fs_desp.errors)
        print("DESP NON FORM:", fs_desp.non_form_errors())

        print("\nKM OK:", fs_km_ok)
        print("KM ERRORS:", fs_km.errors)
        print("KM NON FORM:", fs_km.non_form_errors())
        print("=====================================\n")

        if form_ok and fs_desp_ok and fs_km_ok:
            # salva cabeçalho
            relatorio = form.save()

            # salva formsets vinculados
            fs_desp.instance = relatorio
            fs_km.instance = relatorio

            fs_desp.save()
            fs_km.save()

            messages.success(
                request,
                f"Relatório {relatorio.numero} salvo com sucesso!",
            )
            return redirect("relatorios:relatorio_detail", pk=relatorio.pk)

        messages.error(request, "Corrija os erros indicados antes de salvar.")

    else:
        form = RelatorioTecnicoForm(instance=instance)
        fs_desp = ItemDespesaFormSet(
            instance=instance,
            prefix="despesas",
        )
        fs_km = TrechoKmFormSet(
            instance=instance,
            prefix="trechos",
        )

    # Valor/km vigente para preencher novas linhas
    valor_km_padrao = PoliticaValor.valor_km_vigente(
        instance.data_inicio if instance else datetime.date.today()
    )

    return render(
        request,
        "relatorios/relatorio_form.html",
        {
            "form": form,
            "fs_desp": fs_desp,
            "fs_km": fs_km,
            "instance": instance,
            "titulo_pagina": "Editar Relatório" if instance else "Novo Relatório",
            "acao": "Salvar alterações" if instance else "Criar Relatório",
            "valor_km_padrao": str(valor_km_padrao),
        },
    )


# ─────────────────────────────────────────────
# NOVA LINHA DESPESA (HTMX)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# NOVA LINHA DESPESA
# ─────────────────────────────────────────────
def nova_linha_despesa(request):
    idx = request.GET.get("idx", 0)

    # LINHA ALTERADA: Removido o -{idx} do prefixo
    # form = ItemDespesaForm(prefix=f"despesas-{idx}")  <-- APAGUE ESTA LINHA
    form = ItemDespesaForm(prefix="despesas")

    return render(
        request,
        "partials/_linha_despesa.html",
        {
            "form": form,
            "idx": idx,
        },
    )


# ─────────────────────────────────────────────
# NOVA LINHA KM
# ─────────────────────────────────────────────
def nova_linha_km(request):
    idx = request.GET.get("idx", 0)

    # LINHA ALTERADA: Removido o -{idx} do prefixo
    # form = TrechoKmForm(prefix=f"trechos-{idx}")  <-- APAGUE ESTA LINHA
    form = TrechoKmForm(prefix="trechos")

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


def relatorio_detail_view(request, pk):
    relatorio = get_object_or_404(
        RelatorioTecnico.objects.select_related(
            "cliente", "tecnico_responsavel"
        ).prefetch_related("despesas", "trechos", "equipe__tecnico"),
        pk=pk,
    )
    return render(
        request,
        "relatorios/relatorio_detail.html",
        {
            "relatorio": relatorio,
            "titulo_pagina": f"Relatório {relatorio.numero}",
        },
    )


# ─────────────────────────────────────────────
# EXCLUIR
# ─────────────────────────────────────────────


def relatorio_delete_view(request, pk):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    if request.method == "POST":
        numero = relatorio.numero
        relatorio.delete()
        messages.success(request, f"Relatório {numero} excluído.")
        return redirect("relatorios:relatorio_list")
    return render(
        request,
        "relatorios/relatorio_confirm_delete.html",
        {
            "object": relatorio,
            "titulo_pagina": "Excluir Relatório",
        },
    )


# ─────────────────────────────────────────────
# MUDAR STATUS
# ─────────────────────────────────────────────


@require_POST
def relatorio_status_view(request, pk, status):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    status_validos = [s[0] for s in StatusRelatorio.choices]

    if status not in status_validos:
        messages.error(request, "Status inválido.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    if status == StatusRelatorio.PENDENTE:
        erros = relatorio.pode_enviar()
        if erros:
            for e in erros:
                messages.error(request, e)
            return redirect("relatorios:relatorio_detail", pk=pk)

    relatorio.status = status
    relatorio.save(update_fields=["status", "atualizado_em"])
    messages.success(
        request,
        f'Status alterado para "{relatorio.get_status_display()}".',
    )
    return redirect("relatorios:relatorio_detail", pk=pk)


# ─────────────────────────────────────────────
# TÉCNICOS
# ─────────────────────────────────────────────


class TecnicoListView(ListView):
    model = Tecnico
    template_name = "tecnicos/tecnico_list.html"
    context_object_name = "tecnicos"
    paginate_by = 20

    def get_queryset(self):
        qs = Tecnico.objects.all()
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            qs = qs.filter(Q(nome__icontains=busca) | Q(email__icontains=busca))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Técnicos"
        ctx["busca"] = self.request.GET.get("busca", "")
        return ctx


def tecnico_form_view(request, pk=None):
    instance = get_object_or_404(Tecnico, pk=pk) if pk else None
    form = TecnicoForm(request.POST or None, instance=instance)
    if form.is_valid():
        t = form.save()
        messages.success(request, f"Técnico {t.nome} salvo!")
        return redirect("relatorios:tecnico_list")
    return render(
        request,
        "tecnicos/tecnico_form.html",
        {
            "form": form,
            "instance": instance,
            "titulo_pagina": "Editar Técnico" if instance else "Novo Técnico",
            "acao": "Salvar" if instance else "Cadastrar",
        },
    )


def tecnico_delete_view(request, pk):
    tecnico = get_object_or_404(Tecnico, pk=pk)
    if request.method == "POST":
        tecnico.delete()
        messages.success(request, "Técnico removido.")
        return redirect("relatorios:tecnico_list")
    return render(
        request,
        "tecnicos/tecnico_confirm_delete.html",
        {
            "object": tecnico,
            "titulo_pagina": "Excluir Técnico",
        },
    )


# ─────────────────────────────────────────────
# CLIENTES
# ─────────────────────────────────────────────


class ClienteListView(ListView):
    model = Cliente
    template_name = "clientes/cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 20

    def get_queryset(self):
        qs = Cliente.objects.all()
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(cnpj_cpf__icontains=busca)
                | Q(cidade__icontains=busca)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["titulo_pagina"] = "Clientes"
        ctx["busca"] = self.request.GET.get("busca", "")
        return ctx


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
            "acao": "Salvar" if instance else "Cadastrar",
        },
    )


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


class AdiantamentoListView(ListView):
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
            "acao": "Salvar" if instance else "Registrar",
        },
    )


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
