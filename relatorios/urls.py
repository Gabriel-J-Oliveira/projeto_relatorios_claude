from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    path("completar-cadastro/", views.completar_cadastro_view, name="completar_cadastro"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard/dados/", views.dashboard_dados_json, name="dashboard_dados"),

    # Relatórios
    path("relatorios/", views.RelatorioListView.as_view(), name="relatorio_list"),
    path("relatorios/novo/", views.relatorio_form_view, name="relatorio_create"),
    path("relatorios/<int:pk>/", views.relatorio_detail_view, name="relatorio_detail"),
    path("relatorios/<int:pk>/consulta/", views.relatorio_consulta_view, name="relatorio_consulta"),
    path("relatorios/<int:pk>/pdf-reembolso/", views.relatorio_reembolso_pdf_view, name="relatorio_reembolso_pdf"),
    path("relatorios/<int:pk>/pdf/cliente/<int:cliente_id>/", views.relatorio_cliente_pdf_view, name="relatorio_cliente_pdf"),
    path("relatorios/<int:pk>/pdf/clientes/", views.relatorio_clientes_pdf_view, name="relatorio_clientes_pdf"),
    path("relatorios/<int:pk>/pdf-interno/", views.relatorio_pdf_interno_view, name="relatorio_pdf_interno"),
    path("relatorios/<int:pk>/editar/", views.relatorio_form_view, name="relatorio_update"),
    path("relatorios/<int:pk>/duplicar/", views.relatorio_duplicate_view, name="relatorio_duplicate"),
    path("relatorios/<int:pk>/status/<str:status>/", views.relatorio_status_view, name="relatorio_status"),
    path(
        "relatorios/<int:pk>/itens/<str:tipo>/<int:item_pk>/<str:acao>/",
        views.relatorio_item_financeiro_view,
        name="relatorio_item_financeiro",
    ),
    path(
        "relatorios/<int:pk>/rateio/<str:tipo>/<int:item_pk>/",
        views.relatorio_rateio_financeiro_json,
        name="relatorio_rateio_financeiro",
    ),
    path("anexos/<int:pk>/visualizar/", views.anexo_visualizar_view, name="anexo_visualizar"),
    path("anexos/<int:pk>/baixar/", views.anexo_baixar_view, name="anexo_baixar"),
    path("despesas/<int:pk>/comprovante/visualizar/", views.despesa_comprovante_visualizar_view, name="despesa_comprovante_visualizar"),
    path("despesas/<int:pk>/comprovante/baixar/", views.despesa_comprovante_baixar_view, name="despesa_comprovante_baixar"),
    path("relatorios/importar/listar/", views.relatorio_import_list_json, name="relatorio_import_list"),
    path("relatorios/importar/<int:pk>/", views.relatorio_import_detail_json, name="relatorio_import_detail"),
    path("mapas/buscar-endereco/", views.mapa_buscar_endereco_json, name="mapa_buscar_endereco"),
    path("mapas/calcular-rota/", views.mapa_calcular_rota_json, name="mapa_calcular_rota"),

    # Técnicos
    path("tecnicos/", views.TecnicoListView.as_view(), name="tecnico_list"),
    path("tecnicos/novo/", views.tecnico_form_view, name="tecnico_create"),
    path("tecnicos/<int:pk>/editar/", views.tecnico_form_view, name="tecnico_update"),
    path("tecnicos/<int:pk>/excluir/", views.tecnico_delete_view, name="tecnico_delete"),

    # Clientes
    path("clientes/", views.ClienteListView.as_view(), name="cliente_list"),
    path("clientes/novo/", views.cliente_form_view, name="cliente_create"),
    path("clientes/<int:pk>/editar/", views.cliente_form_view, name="cliente_update"),
    path("clientes/<int:pk>/excluir/", views.cliente_delete_view, name="cliente_delete"),

    # Adiantamentos
    path("adiantamentos/", views.AdiantamentoListView.as_view(), name="adiantamento_list"),
    path( "adiantamentos/novo/", views.adiantamento_form_view, name="adiantamento_create"),
    path("adiantamentos/<int:pk>/editar/", views.adiantamento_form_view, name="adiantamento_update"),
    path("adiantamentos/<int:pk>/excluir/", views.adiantamento_delete_view, name="adiantamento_delete"),
    path("nova-linha-despesa/", views.nova_linha_despesa, name="nova_linha_despesa"),
    path("nova-linha-km/", views.nova_linha_km, name="nova_linha_km"),
]
