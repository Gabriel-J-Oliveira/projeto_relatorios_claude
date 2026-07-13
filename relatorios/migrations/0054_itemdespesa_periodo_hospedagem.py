from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0053_relatorioautosave"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemdespesa",
            name="data_inicio_hospedagem",
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name="Entrada da hospedagem",
            ),
        ),
        migrations.AddField(
            model_name="itemdespesa",
            name="data_fim_hospedagem",
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name="Saída da hospedagem",
            ),
        ),
    ]
