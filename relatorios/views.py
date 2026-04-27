from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import TemplateView, ListView
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Q, Sum, Count
from django.views.decorators.http import require_POST, require_http_methods
from decimal import Decimal

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
    ItemDespesaForm,
    TrechoKmForm,
    TecnicoForm,
    ClienteForm,
    AdiantamentoForm,
    RelatorioFiltroForm,
)


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────


class DashboardView(TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        total = RelatorioTecnico.objects.count()
        total_d = total or 1

        total_despesas = sum(
            r.total_despesas
            for r in RelatorioTecnico.objects.prefetch_related("despesas", "trechos")
        )

        context.update(
            {
                "titulo_pagina": "Dashboard",
                "total_relatorios": total,
                "total_pendentes": RelatorioTecnico.objects.filter(
                    status=StatusRelatorio.PENDENTE
                ).count(),
                "total_tecnicos": Tecnico.objects.filter(ativo=True).count(),
                "total_clientes": Cliente.objects.filter(ativo=True).count(),
                "total_despesas_fmt": f"R$ {total_despesas:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", "."),
                "pct_rascunho": round(
                    RelatorioTecnico.objects.filter(status="rascunho").count()
                    / total_d
                    * 100
                ),
                "pct_pendente": round(
                    RelatorioTecnico.objects.filter(status="pendente").count()
                    / total_d
                    * 100
                ),
                "pct_aprovado": round(
                    RelatorioTecnico.objects.filter(status="aprovado").count()
                    / total_d
                    * 100
                ),
                "pct_faturado": round(
                    RelatorioTecnico.objects.filter(status="faturado").count()
                    / total_d
                    * 100
                ),
                "relatorios_recentes": RelatorioTecnico.objects.select_related(
                    "cliente", "tecnico_responsavel"
                ).order_by("-criado_em")[:8],
            }
        )
        return context


# ─────────────────────────────────────────────
# RELATÓRIO — LISTAGEM
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
# RELATÓRIO — CRIAR / EDITAR (view única com abas)
# ─────────────────────────────────────────────


def relatorio_form_view(request, pk=None):
    """
    View única para criação e edição do relatório.
    Gerencia: cabeçalho + formset de despesas + formset de KM.
    """
    instance = get_object_or_404(RelatorioTecnico, pk=pk) if pk else None
    acao = "Editar" if instance else "Novo"

    if request.method == "POST":
        form = RelatorioTecnicoForm(request.POST, instance=instance)
        fs_desp = ItemDespesaFormSet(request.POST, request.FILES, instance=instance)
        fs_km = TrechoKmFormSet(request.POST, instance=instance)

        if form.is_valid() and fs_desp.is_valid() and fs_km.is_valid():
            relatorio = form.save()
            fs_desp.instance = relatorio
            fs_km.instance = relatorio
            fs_desp.save()
            fs_km.save()
            messages.success(
                request, f"Relatório {relatorio.numero} salvo com sucesso!"
            )
            return redirect("relatorios:relatorio_detail", pk=relatorio.pk)
        else:
            messages.error(request, "Corrija os erros abaixo.")
    else:
        form = RelatorioTecnicoForm(instance=instance)
        fs_desp = ItemDespesaFormSet(instance=instance)
        fs_km = TrechoKmFormSet(instance=instance)

    return render(
        request,
        "relatorios/relatorio_form.html",
        {
            "form": form,
            "fs_desp": fs_desp,
            "fs_km": fs_km,
            "instance": instance,
            "titulo_pagina": f"{acao} Relatório",
            "acao": acao,
            "valor_km_atual": PoliticaValor.valor_km_vigente(
                instance.data_inicio
                if instance
                else __import__("datetime").date.today()
            ),
        },
    )


# ─────────────────────────────────────────────
# RELATÓRIO — DETALHE
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
            "despesas_tecnico": relatorio.despesas.filter(quem_pagou="tecnico"),
            "despesas_empresa": relatorio.despesas.filter(quem_pagou="empresa"),
        },
    )


# ─────────────────────────────────────────────
# RELATÓRIO — EXCLUIR
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
# RELATÓRIO — MUDAR STATUS
# ─────────────────────────────────────────────


@require_POST
def relatorio_status_view(request, pk, status):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    status_validos = [s[0] for s in StatusRelatorio.choices]
    if status not in status_validos:
        messages.error(request, "Status inválido.")
        return redirect("relatorios:relatorio_detail", pk=pk)

    if status == StatusRelatorio.PENDENTE:
        erros = relatorio.pode_fechar()
        if erros:
            for e in erros:
                messages.error(request, e)
            return redirect("relatorios:relatorio_detail", pk=pk)

    relatorio.status = status
    relatorio.save(update_fields=["status", "atualizado_em"])

    # CORREÇÃO DE SINTAXE AQUI:
    messages.success(
        request, f"Status alterado para '{relatorio.get_status_display()}'."
    )
    return redirect("relatorios:relatorio_detail", pk=pk)


# ─────────────────────────────────────────────
# HTMX — LINHA DE DESPESA
# ─────────────────────────────────────────────


def htmx_add_despesa(request, pk):
    """Retorna HTML de uma nova linha de despesa (HTMX)."""
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    total = relatorio.despesas.count()
    prefix = f"despesas-{total}"
    form = ItemDespesaForm(prefix=prefix)
    return render(
        request,
        "./partials/_linha_despesa.html",
        {
            "form": form,
            "prefix": prefix,
            "idx": total,
        },
    )


@require_POST
def htmx_remove_despesa(request, item_pk):
    """Remove um item de despesa via HTMX."""
    item = get_object_or_404(ItemDespesa, pk=item_pk)
    item.delete()
    return HttpResponse("")  # HTMX substitui o elemento por vazio (swap outerHTML)


def htmx_add_trecho(request, pk):
    """Retorna HTML de uma nova linha de trecho KM (HTMX)."""
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    total = relatorio.trechos.count()
    prefix = f"trechos-{total}"
    form = TrechoKmForm(
        prefix=prefix,
        initial={"valor_km": PoliticaValor.valor_km_vigente(relatorio.data_inicio)},
    )
    return render(
        request,
        "./partials/_linha_trecho.html",
        {
            "form": form,
            "prefix": prefix,
            "idx": total,
        },
    )


@require_POST
def htmx_remove_trecho(request, trecho_pk):
    """Remove um trecho KM via HTMX."""
    trecho = get_object_or_404(TrechoKm, pk=trecho_pk)
    trecho.delete()
    return HttpResponse("")


# ─────────────────────────────────────────────
# TÉCNICOS — CRUD
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
        messages.success(request, f"Técnico {t.nome} salvo com sucesso!")
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
# CLIENTES — CRUD
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
        messages.success(request, f"Cliente {c.nome} salvo com sucesso!")
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
# ADIANTAMENTOS — CRUD
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
        total = self.get_queryset().aggregate(total=Sum("valor"))["total"] or Decimal(
            "0.00"
        )
        ctx["total_geral"] = total
        return ctx


def adiantamento_form_view(request, pk=None):
    instance = get_object_or_404(Adiantamento, pk=pk) if pk else None
    form = AdiantamentoForm(request.POST or None, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, "Adiantamento salvo com sucesso!")
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
    adiantamento = get_object_or_404(Adiantamento, pk=pk)
    if request.method == "POST":
        adiantamento.delete()
        messages.success(request, "Adiantamento removido.")
        return redirect("relatorios:adiantamento_list")
    return render(
        request,
        "adiantamentos/adiantamento_confirm_delete.html",
        {
            "object": adiantamento,
            "titulo_pagina": "Excluir Adiantamento",
        },
    )
