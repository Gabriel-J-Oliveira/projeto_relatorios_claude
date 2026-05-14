from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0007_valores_aprovados_financeiro"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="relatoriotecnico",
            name="aprovado_em",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Aprovado em"),
        ),
        migrations.AddField(
            model_name="relatoriotecnico",
            name="aprovado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="relatorios_aprovados",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Aprovado por",
            ),
        ),
    ]
