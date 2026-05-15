from django.db import migrations


GRUPOS_ERP = [
    "Financeiro",
    "Tecnico",
    "Gestor",
    "Administrador ERP",
]


def criar_grupos_erp(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nome in GRUPOS_ERP:
        Group.objects.get_or_create(name=nome)


def remover_grupos_erp(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GRUPOS_ERP).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("relatorios", "0017_numero_oficial_sequencial"),
    ]

    operations = [
        migrations.RunPython(criar_grupos_erp, remover_grupos_erp),
    ]
