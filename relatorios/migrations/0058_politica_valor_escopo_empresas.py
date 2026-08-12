# Generated manually for the policy scope foundation.

from django.db import migrations, models
import django.db.models.deletion


def marcar_politicas_existentes_como_global(apps, schema_editor):
    PoliticaValor = apps.get_model("relatorios", "PoliticaValor")
    PoliticaValor.objects.all().update(escopo="global")


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0057_alter_historicorelatorio_tipo_evento_reaberto"),
    ]

    operations = [
        migrations.AddField(
            model_name="politicavalor",
            name="escopo",
            field=models.CharField(
                choices=[
                    ("global", "Global"),
                    ("empresas", "Empresas específicas"),
                ],
                db_index=True,
                default="global",
                max_length=12,
                verbose_name="Escopo",
            ),
        ),
        migrations.CreateModel(
            name="PoliticaValorEmpresaGrupo",
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
                    "empresa_grupo",
                    models.CharField(
                        choices=[
                            ("blazius_e_lorenzetti", "BLAZIUS E LORENZETTI"),
                            ("controlsul", "CONTROLSUL"),
                            ("fiscalmax", "FISCALMAX"),
                        ],
                        max_length=30,
                        verbose_name="Empresa do grupo",
                    ),
                ),
                (
                    "politica",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="empresas_grupo",
                        to="relatorios.politicavalor",
                        verbose_name="Política",
                    ),
                ),
            ],
            options={
                "verbose_name": "Empresa específica da política",
                "verbose_name_plural": "Empresas específicas da política",
            },
        ),
        migrations.RunPython(
            marcar_politicas_existentes_como_global,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="politicavalorempresagrupo",
            constraint=models.UniqueConstraint(
                fields=("politica", "empresa_grupo"),
                name="uniq_politica_valor_empresa_grupo",
            ),
        ),
    ]
