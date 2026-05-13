# Generated manually to restore the current workflow status choices.

from django.db import migrations, models


def enviado_para_pendente(apps, schema_editor):
    RelatorioTecnico = apps.get_model("relatorios", "RelatorioTecnico")
    RelatorioTecnico.objects.filter(status="enviado").update(status="pendente")


def pendente_para_enviado(apps, schema_editor):
    RelatorioTecnico = apps.get_model("relatorios", "RelatorioTecnico")
    RelatorioTecnico.objects.filter(status="pendente").update(status="enviado")


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0005_alter_cliente_valor_km"),
    ]

    operations = [
        migrations.RunPython(enviado_para_pendente, pendente_para_enviado),
        migrations.AlterField(
            model_name="relatoriotecnico",
            name="status",
            field=models.CharField(
                choices=[
                    ("rascunho", "Rascunho"),
                    ("pendente", "Pendente aprovação"),
                    ("aprovado", "Aprovado"),
                    ("rejeitado", "Rejeitado"),
                    ("faturado", "Faturado"),
                    ("fechado", "Fechado"),
                ],
                default="rascunho",
                max_length=20,
                verbose_name="Status",
            ),
        ),
    ]
