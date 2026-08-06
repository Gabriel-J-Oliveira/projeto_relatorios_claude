from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0056_cidadeatendimento"),
    ]

    operations = [
        migrations.AlterField(
            model_name="historicorelatorio",
            name="tipo_evento",
            field=models.CharField(
                choices=[
                    ("criado", "Relatório criado"),
                    ("enviado", "Relatório enviado para conferência"),
                    ("ajuste_solicitado", "Financeiro solicitou ajustes"),
                    ("reenviado", "Relatório reenviado para conferência"),
                    ("aprovado", "Relatório aprovado"),
                    ("rejeitado", "Relatório rejeitado definitivamente"),
                    ("reaberto", "Relatório reaberto"),
                    ("item_rejeitado", "Item rejeitado pelo financeiro"),
                    ("item_reativado", "Item reativado pelo financeiro"),
                    ("valor_alterado", "Valor aprovado alterado"),
                    ("email_enviado", "Email enviado"),
                    ("email_falha", "Falha no envio de email"),
                ],
                db_index=True,
                default="criado",
                max_length=30,
                verbose_name="Tipo de evento",
            ),
        ),
    ]
