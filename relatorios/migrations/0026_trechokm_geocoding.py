from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0025_alter_relatoriotecnico_valor_adiantamento"),
    ]

    operations = [
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
    ]
