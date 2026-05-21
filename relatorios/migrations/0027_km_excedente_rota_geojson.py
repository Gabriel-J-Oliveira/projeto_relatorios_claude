from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0026_trechokm_geocoding"),
    ]

    operations = [
        migrations.AddField(
            model_name="relatoriotecnico",
            name="km_excedente_interno",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=8,
                validators=[MinValueValidator(Decimal("0.00"))],
                verbose_name="KM excedente / deslocamento interno",
            ),
        ),
        migrations.AddField(
            model_name="relatoriotecnico",
            name="observacao_km_excedente",
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name="Observação do KM excedente",
            ),
        ),
        migrations.AddField(
            model_name="trechokm",
            name="rota_geojson",
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name="Geometria da rota",
            ),
        ),
    ]
