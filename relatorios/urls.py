from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    path("completar-cadastro/", views.completar_cadastro_view, name="completar_cadastro"),
    path("perfil/", views.perfil_usuario_view, name="perfil_usuario"),
    path("ajuda/", views.ajuda_index_view, name="ajuda_index"),
    path("ajuda/categoria/<slug:slug>/", views.ajuda_categoria_view, name="ajuda_categoria"),
    path("ajuda/categoria/<slug:slug>/novo-artigo/", views.ajuda_artigo_criar_view, name="ajuda_artigo_criar"),
    path("ajuda/artigo/<slug:slug>/editar/", views.ajuda_artigo_editar_view, name="ajuda_artigo_editar"),
    path("ajuda/artigo/<slug:slug>/excluir/", views.ajuda_artigo_excluir_view, name="ajuda_artigo_excluir"),
    path("ajuda/imagens/upload/", views.ajuda_imagem_upload_view, name="ajuda_imagem_upload"),
    path("ajuda/imagens/<int:pk>/", views.ajuda_imagem_visualizar_view, name="ajuda_imagem_visualizar"),
    path("ajuda/<slug:slug>/", views.ajuda_artigo_view, name="ajuda_artigo"),
    path("suporte/reportar/", views.suporte_reportar_view, name="suporte_reportar"),
    path("manutencao/", views.manutencao_view, name="manutencao"),
    path("manutencao/emails/<int:pk>/reenviar/", views.manutencao_email_reenviar_view, name="manutencao_email_reenviar"),
    path("manutencao/emails/reenviar-lote/", views.manutencao_emails_reenviar_lote_view, name="manutencao_emails_reenviar_lote"),
    path("manutencao/email-teste/", views.manutencao_email_teste_view, name="manutencao_email_teste"),
    path("tours/marcar-visto/", views.marcar_tour_guiado_visto, name="marcar_tour_guiado_visto"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard/dados/", views.dashboard_dados_json, name="dashboard_dados"),
    path("politica-despesa/", views.politica_despesa_json, name="politica_despesa"),
    path("municipios/buscar/", views.municipios_buscar_json, name="municipios_buscar"),

    # Relatórios
    path("relatorios/", views.RelatorioListView.as_view(), name="relatorio_list"),
    path("relatorios/legados/", views.RelatorioLegadoListView.as_view(), name="relatorio_legado_list"),
    path("relatorios/legados/<int:pk>/", views.relatorio_legado_detail_view, name="relatorio_legado_detail"),
    path("relatorios/novo/", views.relatorio_form_view, name="relatorio_create"),
    path("relatorios/autosave/", views.relatorio_autosave_view, name="relatorio_autosave"),
    path("relatorios/<int:pk>/", views.relatorio_detail_view, name="relatorio_detail"),
    path("relatorios/<int:pk>/consulta/", views.relatorio_consulta_view, name="relatorio_consulta"),
    path("relatorios/<int:pk>/pdf-reembolso/", views.relatorio_reembolso_pdf_view, name="relatorio_reembolso_pdf"),
    path("relatorios/<int:pk>/pdf/cliente/<int:cliente_id>/", views.relatorio_cliente_pdf_view, name="relatorio_cliente_pdf"),
    path("relatorios/<int:pk>/pdf/clientes/", views.relatorio_clientes_pdf_view, name="relatorio_clientes_pdf"),
    path("relatorios/<int:pk>/pdf-interno/", views.relatorio_pdf_interno_view, name="relatorio_pdf_interno"),
    path("relatorios/<int:pk>/editar/", views.relatorio_form_view, name="relatorio_update"),
    path("relatorios/<int:pk>/duplicar/", views.relatorio_duplicate_view, name="relatorio_duplicate"),
    path("relatorios/<int:pk>/excluir-rascunho/", views.relatorio_excluir_rascunho_view, name="relatorio_excluir_rascunho"),
    path("relatorios/<int:pk>/status/<str:status>/", views.relatorio_status_view, name="relatorio_status"),
    path("relatorios/<int:pk>/financeiro/valores/", views.relatorio_valores_financeiros_json, name="relatorio_valores_financeiros"),
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
    path("anexos/<int:pk>/preview/", views.anexo_visualizar_view, name="anexo_preview"),
    path("anexos/<int:pk>/visualizar/", views.anexo_visualizar_view, name="anexo_visualizar"),
    path("anexos/<int:pk>/baixar/", views.anexo_baixar_view, name="anexo_baixar"),
    path("despesas/<int:pk>/comprovante/preview/", views.despesa_comprovante_visualizar_view, name="despesa_comprovante_preview"),
    path("despesas/<int:pk>/comprovante/visualizar/", views.despesa_comprovante_visualizar_view, name="despesa_comprovante_visualizar"),
    path("despesas/<int:pk>/comprovante/baixar/", views.despesa_comprovante_baixar_view, name="despesa_comprovante_baixar"),
    path("relatorios/importar/listar/", views.relatorio_import_list_json, name="relatorio_import_list"),
    path("relatorios/importar/<int:pk>/", views.relatorio_import_detail_json, name="relatorio_import_detail"),
    path("mapas/buscar-endereco/", views.mapa_buscar_endereco_json, name="mapa_buscar_endereco"),
    path("mapas/calcular-rota/", views.mapa_calcular_rota_json, name="mapa_calcular_rota"),

    # Técnicos
    path("tecnicos/", views.TecnicoListView.as_view(), name="tecnico_list"),
    path("tecnicos/<int:pk>/", views.tecnico_detail_view, name="tecnico_detail"),
    path("tecnicos/novo/", views.tecnico_form_view, name="tecnico_create"),
    path("tecnicos/<int:pk>/editar/", views.tecnico_form_view, name="tecnico_update"),
    path("tecnicos/<int:pk>/excluir/", views.tecnico_delete_view, name="tecnico_delete"),

    # Clientes
    path("clientes/", views.ClienteListView.as_view(), name="cliente_list"),
    path("clientes/valor-km/salvar/", views.clientes_valor_km_salvar_view, name="clientes_valor_km_salvar"),
    path("clientes/<int:pk>/valor-km/", views.cliente_valor_km_salvar_view, name="cliente_valor_km_salvar"),
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
