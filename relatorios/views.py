from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import TemplateView, ListView
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST
from decimal import Decimal
import datetime

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
# DASHBOARD
# ─────────────────────────────────────────────

class DashboardView(TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        total_relatorios = RelatorioTecnico.objects.count()
        total_base = total_relatorios or 1

        total_pendentes = RelatorioTecnico.objects.filter(
            status=StatusRelatorio.PENDENTE
        ).count()

        total_adiantamentos = Adiantamento.objects.aggregate(total=Sum("valor"))[
            "total"
        ] or Decimal("0.00")

        total_despesas = RelatorioTecnico.objects.aggregate(
            total=Sum("despesas")
        )["total"] or Decimal("0.00")

        total_tecnicos = Tecnico.objects.filter(ativo=True).count()
        total_clientes = Cliente.objects.filter(ativo=True).count()

        # Detecta automaticamente qual campo de técnico existe no model
        model_fields = {field.name for field in RelatorioTecnico._meta.get_fields()}
        select_related_fields = ["cliente"]

        if "tecnico" in model_fields:
            select_related_fields.append("tecnico")
        elif "tecnico_responsavel" in model_fields:
            select_related_fields.append("tecnico_responsavel")

        relatorios_recentes = RelatorioTecnico.objects.select_related(
            *select_related_fields
        ).order_by("-criado_em")[:8]

        # Percentuais por status: usa apenas os status realmente existentes
        status_keys = [
            "rascunho",
            "pendente",
            "fechado",
            "aprovado",
            "faturado",
        ]

        status_choices = dict(getattr(StatusRelatorio, "choices", []))

        percentuais = {}
        for status in status_keys:
            if status in status_choices:
                percentuais[status] = round(
                    RelatorioTecnico.objects.filter(status=status).count()
                    / total_base
                    * 100
                )
            else:
                percentuais[status] = 0

        def moeda(valor):
            return (
                f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            )

        ctx.update(
            {
                "titulo_pagina": "Dashboard",
                # Valores brutos
                "total_relatorios": total_relatorios,
                "total_pendentes": total_pendentes,
                "total_adiantamentos_valor": total_adiantamentos,
                "total_despesas_valor": total_despesas,
                "total_tecnicos": total_tecnicos,
                "total_clientes": total_clientes,
                # Valores formatados
                "total_adiantamentos": moeda(total_adiantamentos),
                "total_despesas": moeda(total_despesas),
                # Cards da tela
                "cards": [
                    {
                        "titulo": "Total de Relatórios",
                        "valor": total_relatorios,
                        "icone": "bi-file-earmark-text",
                        "cor": "primary",
                        "rodape": "relatórios cadastrados",
                    },
                    {
                        "titulo": "Adiantamentos",
                        "valor": moeda(total_adiantamentos),
                        "icone": "bi-cash-coin",
                        "cor": "success",
                        "rodape": "total lançado",
                    },
                    {
                        "titulo": "Pendentes",
                        "valor": total_pendentes,
                        "icone": "bi-hourglass-split",
                        "cor": "warning",
                        "rodape": "aguardando fechamento",
                    },
                    {
                        "titulo": "Total de Despesas",
                        "valor": moeda(total_despesas),
                        "icone": "bi-graph-up-arrow",
                        "cor": "danger",
                        "rodape": "em despesas registradas",
                    },
                ],
                # Lista recente
                "relatorios_recentes": relatorios_recentes,
                # Percentuais para barras / progresso
                "pct_rascunho": percentuais.get("rascunho", 0),
                "pct_pendente": percentuais.get("pendente", 0),
                "pct_fechado": percentuais.get("fechado", 0),
                "pct_aprovado": percentuais.get("aprovado", 0),
                "pct_faturado": percentuais.get("faturado", 0),
                # Apoio visual / estatísticas extras
                "percentuais_status": percentuais,
            }
        )

        return ctx


def _form_has_content(form):
    if not hasattr(form, "cleaned_data"):
        return False
    if form.cleaned_data.get("DELETE"):
        return False

    values = []
    for key, value in form.cleaned_data.items():
        if key in {"DELETE", "id", "relatorio"}:
            continue
        values.append(value)

    return any(value not in (None, "", [], ()) for value in values)


def _sync_tecnicos_apoio(relatorio, tecnicos_apoio):
    RelatorioTecnicoTecnico.objects.filter(
        relatorio=relatorio,
        papel=PapelTecnico.APOIO,
    ).delete()

    for tecnico in tecnicos_apoio:
        RelatorioTecnicoTecnico.objects.create(
            relatorio=relatorio,
            tecnico=tecnico,
            papel=PapelTecnico.APOIO,
        )


def _render_form(request, *, instance=None):
    instance = instance or RelatorioTecnico()

    if request.method == "POST":
        form = RelatorioTecnicoForm(request.POST, request.FILES, instance=instance)
        despesas_formset = ItemDespesaFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="despesas",
        )
        km_formset = TrechoKmFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="kms",
        )

        if form.is_valid() and despesas_formset.is_valid() and km_formset.is_valid():
            status = form.cleaned_data.get("status")
            tem_despesa = any(_form_has_content(f) for f in despesas_formset.forms)

            if (
                status in {StatusRelatorio.FECHADO, StatusRelatorio.FATURADO}
                and not tem_despesa
            ):
                form.add_error(
                    None,
                    "É necessário informar ao menos um item de despesa para fechar o relatório.",
                )
            else:
                if status == StatusRelatorio.FATURADO:
                    for f in despesas_formset.forms:
                        if not _form_has_content(f):
                            continue
                        comprovante = f.cleaned_data.get("comprovante") or getattr(
                            f.instance, "comprovante", None
                        )
                        if not comprovante:
                            f.add_error(
                                "comprovante",
                                "Comprovante obrigatório para status Faturado.",
                            )
                            break

                if not form.errors and not despesas_formset.errors:
                    with transaction.atomic():
                        relatorio = form.save(commit=False)
                        relatorio.save()

                        tecnicos_apoio = form.cleaned_data.get("tecnicos_apoio", [])
                        _sync_tecnicos_apoio(relatorio, tecnicos_apoio)

                        despesas_formset.instance = relatorio
                        kms_formset.instance = relatorio

                        itens = despesas_formset.save(commit=False)
                        for item in itens:
                            item.relatorio = relatorio
                            item.save()
                        for obj in despesas_formset.deleted_objects:
                            obj.delete()

                        trechos = kms_formset.save(commit=False)
                        for trecho in trechos:
                            trecho.relatorio = relatorio
                            trecho.save()
                        for obj in kms_formset.deleted_objects:
                            obj.delete()

                        relatorio.recalcular_totais(commit=True)

                        messages.success(
                            request, f"Relatório {relatorio.numero} salvo com sucesso."
                        )
                        return redirect("relatorios:relatorio_list")

        messages.error(request, "Corrija os erros abaixo antes de salvar.")

    else:
        form = RelatorioTecnicoForm(instance=instance)
        despesas_formset = ItemDespesaFormSet(instance=instance, prefix="despesas")
        km_formset = TrechoKmFormSet(instance=instance, prefix="kms")

    return render(
        request,
        "relatorios/relatorio_form.html",
        {
            "titulo_pagina": (
                "Novo Relatório"
                if not instance.pk
                else f"Editar Relatório {instance.numero}"
            ),
            "acao": "Criar" if not instance.pk else "Salvar alterações",
            "form": form,
            "despesas_formset": despesas_formset,
            "km_formset": km_formset,
            "relatorio": instance,
        },
    )


def relatorio_create(request):
    return _render_form(request)


def relatorio_update(request, pk):
    relatorio = get_object_or_404(RelatorioTecnico, pk=pk)
    return _render_form(request, instance=relatorio)


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

    Fluxo Single-Save:
    - Formulário principal + formsets de despesas e KM
      são submetidos e validados juntos em um único POST.
    - Não existe mais a obrigatoriedade de salvar o cabeçalho
      antes de adicionar itens. Os itens são adicionados via
      JavaScript puro (clonagem de linha) e submetidos junto
      com o formulário principal.
    """
    instance = get_object_or_404(RelatorioTecnico, pk=pk) if pk else None

    if request.method == "POST":
        form = RelatorioTecnicoForm(request.POST, instance=instance)
        fs_desp = ItemDespesaFormSet(
            request.POST,
            request.FILES,
            instance=instance,
            prefix="despesas",
        )
        fs_km = TrechoKmFormSet(
            request.POST,
            instance=instance,
            prefix="trechos",
        )

        form_ok = form.is_valid()
        fs_desp_ok = fs_desp.is_valid()
        fs_km_ok = fs_km.is_valid()

        if form_ok and fs_desp_ok and fs_km_ok:
            relatorio = form.save()  # salva cabeçalho + equipe M2M

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
        fs_desp = ItemDespesaFormSet(instance=instance, prefix="despesas")
        fs_km = TrechoKmFormSet(instance=instance, prefix="trechos")

    # Valor/km vigente para preencher novas linhas de KM via JS
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


def nova_linha_despesa(request):
    idx = request.GET.get("idx", 0)
    form = ItemDespesaForm(prefix=f"despesas-{idx}")

    return render(
        request,
        "partials/_linha_despesa.html",
        {
            "form": form,
            "idx": idx,
        },
    )


def nova_linha_km(request):
    idx = request.GET.get("idx", 0)

    form = TrechoKmForm(prefix=f"trechos-{idx}")

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
