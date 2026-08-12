from django.db import migrations, models


EMPRESA_GRUPO_CHOICES = [
    ("blazius_e_lorenzetti", "BLAZIUS E LORENZETTI"),
    ("controlsul", "CONTROLSUL"),
    ("fiscalmax", "FISCALMAX"),
    ("casa_chico_de_pneus", "CASA CHICO DE PNEUS"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0059_politica_valor_escopo_empresas"),
    ]

    operations = [
        migrations.AlterField(
            model_name="politicavalorempresagrupo",
            name="empresa_grupo",
            field=models.CharField(
                choices=EMPRESA_GRUPO_CHOICES,
                max_length=30,
                verbose_name="Empresa do grupo",
            ),
        ),
        migrations.AlterField(
            model_name="relatoriotecnico",
            name="empresa_grupo",
            field=models.CharField(
                blank=True,
                choices=EMPRESA_GRUPO_CHOICES,
                max_length=30,
                verbose_name="Empresa do grupo",
            ),
        ),
    ]
