import logging

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.utils.html import format_html
from django.db.models import Sum
from .models import (
    Tecnico,
    Cliente,
    Municipio,
    PoliticaValor,
    RelatorioTecnico,
    RelatorioSnapshotFinanceiro,
    RelatorioTecnicoEquipe,
    StatusRelatorio,
    ItemDespesa,
    TrechoKm,
    Adiantamento,
    AnexoRelatorio,
    PerfilUsuario,
    Setor,
    UsuarioSetorImportado,
    CategoriaAjuda,
    ArtigoAjuda,
    ImagemAjuda,
)


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TECNICO
# ─────────────────────────────────────────────


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ["usuario", "setor", "funcao_setor", "setor_confirmado", "setor_origem", "cadastro_confirmado_em", "atualizado_em"]
    list_filter = ["setor", "setor_confirmado", "setor_origem"]
    search_fields = ["usuario__username", "usuario__first_name", "usuario__last_name", "usuario__email", "funcao_setor"]
    readonly_fields = ["criado_em", "atualizado_em", "setor_atualizado_em", "setor_atualizado_por"]


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
    list_display = ["nome", "slug", "ativo", "atualizado_em"]
    list_filter = ["ativo"]
    search_fields = ["nome", "slug"]
    prepopulated_fields = {"slug": ("nome",)}
    readonly_fields = ["criado_em", "atualizado_em"]


@admin.register(UsuarioSetorImportado)
class UsuarioSetorImportadoAdmin(admin.ModelAdmin):
    list_display = [
        "nome",
        "setor",
        "funcao",
        "ativo",
        "status",
        "usuario_vinculado",
        "tecnico_vinculado",
        "aplicado_em",
    ]
    list_filter = ["ativo", "status", "setor"]
    search_fields = [
        "nome",
        "nome_normalizado",
        "funcao",
        "usuario_vinculado__username",
        "tecnico_vinculado__nome",
    ]
    readonly_fields = ["nome_normalizado", "aplicado_em", "criado_em", "atualizado_em"]


@admin.register(CategoriaAjuda)
class CategoriaAjudaAdmin(admin.ModelAdmin):
    list_display = ["titulo", "slug", "ordem", "ativo", "atualizado_em"]
    list_filter = ["ativo"]
    search_fields = ["titulo", "slug", "descricao"]
    prepopulated_fields = {"slug": ("titulo",)}
    list_editable = ["ordem", "ativo"]
    readonly_fields = ["criado_em", "atualizado_em"]


@admin.register(ArtigoAjuda)
class ArtigoAjudaAdmin(admin.ModelAdmin):
    list_display = ["titulo", "categoria", "ativo", "importante", "link_rapido", "atualizado_em"]
    list_filter = ["ativo", "categoria", "importante", "link_rapido", "formato"]
    search_fields = ["titulo", "slug", "resumo", "conteudo"]
    prepopulated_fields = {"slug": ("titulo",)}
    readonly_fields = ["criado_em", "atualizado_em", "criado_por", "atualizado_por"]


@admin.register(ImagemAjuda)
class ImagemAjudaAdmin(admin.ModelAdmin):
    list_display = ["nome_original", "artigo", "tipo_mime", "tamanho_bytes", "enviado_por", "criado_em"]
    list_filter = ["tipo_mime", "criado_em"]
    search_fields = ["nome_original", "arquivo", "artigo__titulo"]
    readonly_fields = ["nome_original", "tipo_mime", "tamanho_bytes", "enviado_por", "criado_em"]


@admin.register(Tecnico)
class TecnicoAdmin(admin.ModelAdmin):
    list_display = ["nome", "email", "setor", "funcao_setor", "setor_confirmado", "telefone", "ativo"]
    list_filter = ["ativo", "setor", "setor_confirmado", "setor_origem"]
    search_fields = ["nome", "email", "funcao_setor"]
    list_editable = ["ativo"]


# ─────────────────────────────────────────────
# CLIENTE
# ─────────────────────────────────────────────


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = [
        "nome_exibicao_admin",
        "cnpj_cpf",
        "cidade",
        "uf",
        "valor_km",
        "origem_api",
        "sincronizado_em",
        "ativo",
    ]
    list_filter = ["ativo", "origem_api", "uf"]
    search_fields = ["nome", "razao_social", "nome_fantasia", "cnpj_cpf", "cidade"]
    list_editable = ["ativo"]
    readonly_fields = [
        "api_created_at",
        "api_updated_at",
        "sincronizado_em",
        "hash_dados_api",
        "valor_km_atualizado_em",
        "valor_km_atualizado_por",
    ]

    @admin.display(description="Cliente", ordering="nome")
    def nome_exibicao_admin(self, obj):
        return obj.nome_exibicao


# ─────────────────────────────────────────────
@admin.register(Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display = [
        "nome",
        "uf",
        "uf_nome",
        "codigo_ibge",
        "eh_capital",
        "tipo_localidade_padrao",
        "ativo",
    ]
    list_filter = ["ativo", "uf", "eh_capital", "tipo_localidade_padrao"]
    search_fields = ["nome", "nome_normalizado", "codigo_ibge", "aliases"]
    list_editable = ["tipo_localidade_padrao", "ativo"]
    readonly_fields = ["nome_normalizado", "criado_em", "atualizado_em"]


# POLITICA DE VALORES
# ─────────────────────────────────────────────


@admin.register(PoliticaValor)
class PoliticaValorAdmin(admin.ModelAdmin):
    list_display = [
        "chave",
        "descricao",
        "tipo_politica",
        "tipo_despesa",
        "tipo_localidade",
        "cidade",
        "limite_valor",
        "valor_km",
        "vigencia_inicio",
        "vigencia_fim",
        "ativo",
    ]
    list_filter = ["ativo", "tipo_politica", "tipo_despesa", "tipo_localidade", "cidade"]
    search_fields = ["chave", "descricao", "cidade", "origem", "destino"]
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


@admin.register(AnexoRelatorio)
class AnexoRelatorioAdmin(admin.ModelAdmin):
    list_display = [
        "nome_original",
        "relatorio",
        "despesa",
        "trecho",
        "tipo_mime",
        "tamanho_bytes",
        "enviado_por",
        "criado_em",
    ]
    list_filter = ["tipo_mime", "criado_em"]
    search_fields = [
        "nome_original",
        "arquivo",
        "relatorio__numero",
        "relatorio__cliente__nome",
    ]
    readonly_fields = [
        "nome_original",
        "tipo_mime",
        "tamanho_bytes",
        "enviado_por",
        "criado_em",
    ]


@admin.register(RelatorioTecnico)
class RelatorioTecnicoAdmin(admin.ModelAdmin):
    list_display = [
        "numero",
        "tipo_relatorio",
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
        "tipo_relatorio",
        "tipo_localidade",
        "municipio_atendimento",
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

    def _obj_finalizado(self, obj):
        return obj and obj.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}

    def has_delete_permission(self, request, obj=None):
        return False

    def get_inline_instances(self, request, obj=None):
        if self._obj_finalizado(obj):
            return []
        return super().get_inline_instances(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if self._obj_finalizado(obj):
            fields.extend(field.name for field in obj._meta.fields)
        return list(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        if change:
            anterior = RelatorioTecnico.objects.filter(pk=obj.pk).only("status").first()
            if anterior and anterior.status in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}:
                logger.warning(
                    "Tentativa de alterar relatorio finalizado via admin. relatorio=%s usuario=%s",
                    obj.pk,
                    getattr(request.user, "pk", None),
                )
                raise PermissionDenied("Relatorio finalizado nao pode ser alterado.")
        super().save_model(request, obj, form, change)

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
                    "municipio_atendimento",
                    ("cidade_atendimento", "uf_atendimento", "tipo_localidade"),
                    (
                        "cidade_atendimento_normalizada",
                        "uf_atendimento_normalizada",
                        "tipo_localidade_calculada",
                    ),
                    ("localidade_override", "motivo_override_localidade"),
                    ("data_inicio", "data_fim"),
                    "motivo",
                    "tipo_relatorio",
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
            "conferencia_pendente": "#ffc107",
            "ajuste_pendente": "#fd7e14",
            "aprovado": "#198754",
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


@admin.register(RelatorioSnapshotFinanceiro)
class RelatorioSnapshotFinanceiroAdmin(admin.ModelAdmin):
    list_display = [
        "numero",
        "status",
        "total_solicitado",
        "total_aprovado",
        "diferenca_removida",
        "finalizado_em",
        "finalizado_por",
    ]
    list_filter = ["status", "finalizado_em"]
    search_fields = ["numero", "relatorio__numero"]
    readonly_fields = [
        "relatorio",
        "schema_version",
        "numero",
        "status",
        "total_solicitado",
        "total_aprovado",
        "diferenca_removida",
        "payload",
        "checksum",
        "finalizado_em",
        "finalizado_por",
        "criado_em",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False
