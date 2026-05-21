from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0025_alter_relatoriotecnico_valor_adiantamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="trechokm",
            name="origem_endereco_completo",
            field=models.CharField(
                "Endereço completo da origem",
                blank=True,
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="origem_lat",
            field=models.DecimalField(
                "Latitude origem",
                blank=True,
                decimal_places=7,
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="origem_lon",
            field=models.DecimalField(
                "Longitude origem",
                blank=True,
                decimal_places=7,
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="destino_endereco_completo",
            field=models.CharField(
                "Endereço completo do destino",
                blank=True,
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="destino_lat",
            field=models.DecimalField(
                "Latitude destino",
                blank=True,
                decimal_places=7,
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="destino_lon",
            field=models.DecimalField(
                "Longitude destino",
                blank=True,
                decimal_places=7,
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="km_calculado_api",
            field=models.DecimalField(
                "KM calculado pela API",
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="km_informado",
            field=models.DecimalField(
                "KM informado",
                blank=True,
                decimal_places=2,
                max_digits=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="diferenca_km_percentual",
            field=models.DecimalField(
                "Diferença KM (%)",
                blank=True,
                decimal_places=2,
                max_digits=7,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="fonte_calculo_rota",
            field=models.CharField(
                "Fonte do cálculo da rota",
                blank=True,
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="calculado_em",
            field=models.DateTimeField(
                "Calculado em",
                blank=True,
                null=True,
            ),
        ),
    ]
