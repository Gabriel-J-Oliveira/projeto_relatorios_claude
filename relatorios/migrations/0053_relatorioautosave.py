from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0052_emaillog_reenviado_por_emaillog_ultimo_reenvio_em"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RelatorioAutoSave",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chave", models.CharField(db_index=True, max_length=80)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("arquivos", models.JSONField(blank=True, default=list)),
                ("pagina", models.CharField(blank=True, max_length=500)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("campos_count", models.PositiveIntegerField(default=0)),
                ("despesas_count", models.PositiveIntegerField(default=0)),
                ("trechos_count", models.PositiveIntegerField(default=0)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "relatorio",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="autosaves",
                        to="relatorios.relatoriotecnico",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="autosaves_relatorios",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "AutoSave de relatorio",
                "verbose_name_plural": "AutoSaves de relatorios",
                "unique_together": {("usuario", "chave")},
            },
        ),
        migrations.AddIndex(
            model_name="relatorioautosave",
            index=models.Index(fields=["usuario", "chave"], name="relatorios__usuario_02e670_idx"),
        ),
        migrations.AddIndex(
            model_name="relatorioautosave",
            index=models.Index(fields=["relatorio", "atualizado_em"], name="relatorios__relator_9ad763_idx"),
        ),
    ]
