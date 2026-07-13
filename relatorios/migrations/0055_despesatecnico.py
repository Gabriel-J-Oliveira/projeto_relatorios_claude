from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0054_itemdespesa_periodo_hospedagem"),
    ]

    operations = [
        migrations.CreateModel(
            name="DespesaTecnico",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "despesa",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tecnicos_vinculados",
                        to="relatorios.itemdespesa",
                    ),
                ),
                (
                    "tecnico",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="despesas_participantes",
                        to="relatorios.tecnico",
                    ),
                ),
            ],
            options={
                "verbose_name": "Técnico da Despesa",
                "verbose_name_plural": "Técnicos da Despesa",
                "unique_together": {("despesa", "tecnico")},
            },
        ),
    ]
