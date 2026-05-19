from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("relatorios", "0018_grupos_erp_autorizacao"),
    ]

    operations = [
        migrations.AddField(
            model_name="relatoriotecnico",
            name="criado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="relatorios_criados",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Criado por",
            ),
        ),
    ]
