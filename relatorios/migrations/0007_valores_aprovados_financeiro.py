from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0006_restore_relatorio_status_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemdespesa",
            name="valor_aprovado",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Valor aprovado (R$)",
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="valor_km_aprovado",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Valor por km aprovado (R$)",
            ),
        ),
    ]
