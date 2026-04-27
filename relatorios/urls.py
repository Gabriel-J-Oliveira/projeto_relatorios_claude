from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    # Dashboard
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    # Relatórios
    path("relatorios/", views.RelatorioListView.as_view(), name="relatorio_list"),
    path("relatorios/novo/", views.relatorio_form_view, name="relatorio_create"),
    path("relatorios/<int:pk>/", views.relatorio_detail_view, name="relatorio_detail"),
    path(
        "relatorios/<int:pk>/editar/",
        views.relatorio_form_view,
        name="relatorio_update",
    ),
    path(
        "relatorios/<int:pk>/excluir/",
        views.relatorio_delete_view,
        name="relatorio_delete",
    ),
    path(
        "relatorios/<int:pk>/status/<str:status>/",
        views.relatorio_status_view,
        name="relatorio_status",
    ),
    # HTMX — despesas
    path(
        "relatorios/<int:pk>/htmx/despesa/add/",
        views.htmx_add_despesa,
        name="htmx_add_despesa",
    ),
    path(
        "relatorios/htmx/despesa/<int:item_pk>/remove/",
        views.htmx_remove_despesa,
        name="htmx_remove_despesa",
    ),
    # HTMX — trechos km
    path(
        "relatorios/<int:pk>/htmx/trecho/add/",
        views.htmx_add_trecho,
        name="htmx_add_trecho",
    ),
    path(
        "relatorios/htmx/trecho/<int:trecho_pk>/remove/",
        views.htmx_remove_trecho,
        name="htmx_remove_trecho",
    ),
    # Técnicos
    path("tecnicos/", views.TecnicoListView.as_view(), name="tecnico_list"),
    path("tecnicos/novo/", views.tecnico_form_view, name="tecnico_create"),
    path("tecnicos/<int:pk>/editar/", views.tecnico_form_view, name="tecnico_update"),
    path(
        "tecnicos/<int:pk>/excluir/", views.tecnico_delete_view, name="tecnico_delete"
    ),
    # Clientes
    path("clientes/", views.ClienteListView.as_view(), name="cliente_list"),
    path("clientes/novo/", views.cliente_form_view, name="cliente_create"),
    path("clientes/<int:pk>/editar/", views.cliente_form_view, name="cliente_update"),
    path(
        "clientes/<int:pk>/excluir/", views.cliente_delete_view, name="cliente_delete"
    ),
    # Adiantamentos
    path(
        "adiantamentos/", views.AdiantamentoListView.as_view(), name="adiantamento_list"
    ),
    path(
        "adiantamentos/novo/", views.adiantamento_form_view, name="adiantamento_create"
    ),
    path(
        "adiantamentos/<int:pk>/editar/",
        views.adiantamento_form_view,
        name="adiantamento_update",
    ),
    path(
        "adiantamentos/<int:pk>/excluir/",
        views.adiantamento_delete_view,
        name="adiantamento_delete",
    ),
]
