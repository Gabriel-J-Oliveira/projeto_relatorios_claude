from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0062_empresa_grupo_cliente"),
    ]

    operations = [
        migrations.AddField(
            model_name="anexorelatorio",
            name="sha256",
            field=models.CharField(blank=True, db_index=True, max_length=64, verbose_name="SHA-256"),
        ),
        migrations.AddIndex(
            model_name="anexorelatorio",
            index=models.Index(fields=["despesa", "sha256"], name="relatorios__despesa_6566ad_idx"),
        ),
        migrations.AddIndex(
            model_name="anexorelatorio",
            index=models.Index(fields=["trecho", "sha256"], name="relatorios__trecho__bc1f24_idx"),
        ),
        migrations.AddConstraint(
            model_name="anexorelatorio",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    despesa__isnull=False,
                    sha256__gt="",
                    trecho__isnull=True,
                ),
                fields=("despesa", "sha256"),
                name="anexo_desp_sha_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="anexorelatorio",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    despesa__isnull=True,
                    sha256__gt="",
                    trecho__isnull=False,
                ),
                fields=("trecho", "sha256"),
                name="anexo_trecho_sha_uniq",
            ),
        ),
    ]
