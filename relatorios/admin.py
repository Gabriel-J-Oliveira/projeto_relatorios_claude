from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum
from .models import (
    Tecnico,
    Cliente,
    PoliticaValor,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    ItemDespesa,
    TrechoKm,
    Adiantamento,
)


# ─────────────────────────────────────────────
# TECNICO
# ─────────────────────────────────────────────


@admin.register(Tecnico)
class TecnicoAdmin(admin.ModelAdmin):
    list_display = ["nome", "email", "telefone", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["nome", "email"]
    list_editable = ["ativo"]


# ─────────────────────────────────────────────
# CLIENTE
# ─────────────────────────────────────────────


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ["nome", "cnpj_cpf", "cidade", "uf", "ativo"]
    list_filter = ["ativo", "uf"]
    search_fields = ["nome", "cnpj_cpf"]
    list_editable = ["ativo"]


# ─────────────────────────────────────────────
# POLITICA DE VALORES
# ─────────────────────────────────────────────


@admin.register(PoliticaValor)
class PoliticaValorAdmin(admin.ModelAdmin):
    list_display = [
        "descricao",
        "tipo_despesa",
        "limite_valor",
        "valor_km",
        "vigencia_inicio",
        "vigencia_fim",
        "ativo",
    ]
    list_filter = ["ativo", "tipo_despesa"]
    list_editable = ["ativo"]


# ─────────────────────────────────────────────
# INLINES do Relatório
# ─────────────────────────────────────────────


class EquipeInline(admin.TabularInline):
    model = RelatorioTecnicoEquipe
    extra = 1
    fields = ["tecnico", "papel"]


class ItemDespesaInline(admin.TabularInline):
    model = ItemDespesa
    extra = 1
    fields = [
        "ordem",
        "data",
        "tipo",
        "descricao",
        "valor",
        "quem_pagou",
        "comprovante",
        "observacoes",
    ]


class TrechoKmInline(admin.TabularInline):
    model = TrechoKm
    extra = 1
    readonly_fields = ["valor_calculado"]
    fields = [
        "ordem",
        "data",
        "origem",
        "destino",
        "km",
        "valor_km",
        "valor_calculado",
        "observacao",
    ]


# ─────────────────────────────────────────────
# RELATORIO TECNICO
# ─────────────────────────────────────────────


@admin.register(RelatorioTecnico)
class RelatorioTecnicoAdmin(admin.ModelAdmin):
    list_display = [
        "numero",
        "cliente",
        "tecnico_responsavel",
        "data_inicio",
        "data_fim",
        "col_total_despesas",
        "col_adiantamento",
        "col_saldo",
        "col_status",
    ]
    list_filter = [
        "status",
        "tipo_localidade",
        "tecnico_responsavel",
        "cliente",
    ]
    search_fields = ["numero", "cliente__nome", "tecnico_responsavel__nome"]
    date_hierarchy = "data_inicio"
    readonly_fields = [
        "criado_em",
        "atualizado_em",
        "col_total_despesas",
        "col_adiantamento",
        "col_saldo",
    ]
    inlines = [EquipeInline, ItemDespesaInline, TrechoKmInline]

    fieldsets = (
        (
            "Identificação",
            {
                "fields": ("numero", "status"),
            },
        ),
        (
            "Atendimento",
            {
                "fields": (
                    "cliente",
                    ("tecnico_responsavel",),
                    ("cidade_atendimento", "uf_atendimento", "tipo_localidade"),
                    ("data_inicio", "data_fim"),
                    "motivo",
                    "centro_custo",
                ),
            },
        ),
        (
            "Financeiro",
            {
                "fields": (
                    "valor_adiantamento",
                    "col_total_despesas",
                    "col_saldo",
                ),
            },
        ),
        (
            "Observações",
            {
                "fields": ("observacoes",),
                "classes": ("collapse",),
            },
        ),
        (
            "Auditoria",
            {
                "fields": ("criado_em", "atualizado_em"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Total Despesas")
    def col_total_despesas(self, obj):
        return f"R$ {obj.total_despesas:.2f}"

    @admin.display(description="Adiantamento")
    def col_adiantamento(self, obj):
        return f"R$ {obj.valor_adiantamento:.2f}"

    @admin.display(description="Saldo")
    def col_saldo(self, obj):
        saldo = obj.saldo
        cor = "green" if saldo >= 0 else "red"
        return format_html(
            '<strong style="color:{}">{}</strong>',
            cor,
            f"R$ {saldo:.2f}",
        )

    @admin.display(description="Status")
    def col_status(self, obj):
        cores = {
            "rascunho": "#6c757d",
            "pendente": "#ffc107",
            "aprovado": "#198754",
            "rejeitado": "#dc3545",
            "faturado": "#0dcaf0",
            "fechado": "#0dcaf0",
            "rejeitado": "#dc3545",
        }
        cor = cores.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;'
            'border-radius:12px;font-size:.8rem;font-weight:600">{}</span>',
            cor,
            obj.get_status_display(),
        )


# ─────────────────────────────────────────────
# ADIANTAMENTO
# ─────────────────────────────────────────────


@admin.register(Adiantamento)
class AdiantamentoAdmin(admin.ModelAdmin):
    list_display = ["tecnico", "tipo", "valor", "data", "relatorio"]
    list_filter = ["tipo", "tecnico"]
    search_fields = ["tecnico__nome", "descricao"]
