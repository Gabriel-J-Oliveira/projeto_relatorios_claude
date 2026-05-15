import django.utils.timezone
from django.db import migrations, models


EVENTOS_LEGADOS = {
    "Relatório criado": "criado",
    "Relatório enviado para conferência": "enviado",
    "Relatório reenviado para conferência": "reenviado",
    "Financeiro solicitou ajustes": "ajuste_solicitado",
    "Relatório rejeitado definitivamente": "rejeitado",
    "Relatório aprovado": "aprovado",
    "Despesa rejeitada pelo financeiro": "item_rejeitado",
    "Trecho KM rejeitado pelo financeiro": "item_rejeitado",
    "Item rejeitado pelo financeiro": "item_rejeitado",
    "Item restaurado pelo financeiro": "item_reativado",
    "Item reativado pelo financeiro": "item_reativado",
    "Valor aprovado alterado": "valor_alterado",
}


def migrar_historico_existente(apps, schema_editor):
    HistoricoRelatorio = apps.get_model("relatorios", "HistoricoRelatorio")
    for historico in HistoricoRelatorio.objects.all().iterator():
        historico.tipo_evento = EVENTOS_LEGADOS.get(historico.acao, "criado")
        historico.data_hora = historico.created_at
        if historico.dados_json is None:
            historico.dados_json = {}
        historico.save(update_fields=["tipo_evento", "data_hora", "dados_json"])


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0015_itemdespesa_motivo_rejeicao_itemdespesa_rejeitado_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicorelatorio",
            name="tipo_evento",
            field=models.CharField(
                choices=[
                    ("criado", "Relatório criado"),
                    ("enviado", "Relatório enviado para conferência"),
                    ("ajuste_solicitado", "Financeiro solicitou ajustes"),
                    ("reenviado", "Relatório reenviado para conferência"),
                    ("aprovado", "Relatório aprovado"),
                    ("rejeitado", "Relatório rejeitado definitivamente"),
                    ("item_rejeitado", "Item rejeitado pelo financeiro"),
                    ("item_reativado", "Item reativado pelo financeiro"),
                    ("valor_alterado", "Valor aprovado alterado"),
                ],
                db_index=True,
                default="criado",
                max_length=30,
                verbose_name="Tipo de evento",
            ),
        ),
        migrations.AddField(
            model_name="historicorelatorio",
            name="data_hora",
            field=models.DateTimeField(
                auto_now_add=True,
                db_index=True,
                default=django.utils.timezone.now,
                verbose_name="Data/hora",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="historicorelatorio",
            name="dados_json",
            field=models.JSONField(blank=True, default=dict, verbose_name="Dados JSON"),
        ),
        migrations.RunPython(migrar_historico_existente, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="historicorelatorio",
            options={
                "ordering": ["-data_hora", "-created_at"],
                "verbose_name": "Histórico do Relatório",
                "verbose_name_plural": "Históricos dos Relatórios",
            },
        ),
    ]
