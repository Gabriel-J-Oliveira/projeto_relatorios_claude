import re
from django.db import migrations, models


def extrair_sequencial(numero):
    match = re.search(r"(\d+)$", str(numero or ""))
    return int(match.group(1)) if match else 0


def criar_contador_relatorio(apps, schema_editor):
    RelatorioTecnico = apps.get_model("relatorios", "RelatorioTecnico")
    SequencialRelatorio = apps.get_model("relatorios", "SequencialRelatorio")

    maior = 0
    for numero in RelatorioTecnico.objects.exclude(numero__isnull=True).values_list(
        "numero", flat=True
    ):
        maior = max(maior, extrair_sequencial(numero))

    SequencialRelatorio.objects.update_or_create(
        chave="relatorio_oficial",
        defaults={"proximo_numero": maior + 1},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0016_historico_operacional"),
    ]

    operations = [
        migrations.AlterField(
            model_name="relatoriotecnico",
            name="numero",
            field=models.CharField(
                blank=True,
                max_length=30,
                null=True,
                unique=True,
                verbose_name="Número",
            ),
        ),
        migrations.CreateModel(
            name="SequencialRelatorio",
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
                ("chave", models.CharField(max_length=50, unique=True, verbose_name="Chave")),
                (
                    "proximo_numero",
                    models.PositiveIntegerField(default=1, verbose_name="Próximo número"),
                ),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Sequencial de Relatório",
                "verbose_name_plural": "Sequenciais de Relatórios",
            },
        ),
        migrations.RunPython(criar_contador_relatorio, migrations.RunPython.noop),
    ]
