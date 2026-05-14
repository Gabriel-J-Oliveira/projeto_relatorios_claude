from decimal import Decimal

from django.db import migrations, models


def normalizar_valores_aprovados(apps, schema_editor):
    RelatorioTecnico = apps.get_model("relatorios", "RelatorioTecnico")
    ItemDespesa = apps.get_model("relatorios", "ItemDespesa")
    TrechoKm = apps.get_model("relatorios", "TrechoKm")

    relatorios_aprovados = RelatorioTecnico.objects.filter(
        status__in=["aprovado", "faturado"]
    )

    ItemDespesa.objects.filter(
        relatorio__in=relatorios_aprovados,
        valor_aprovado__isnull=True,
    ).update(valor_aprovado=models.F("valor"))

    for trecho in TrechoKm.objects.filter(
        relatorio__in=relatorios_aprovados,
        valor_km_aprovado__isnull=True,
    ).iterator():
        trecho.valor_km_aprovado = trecho.valor_km.quantize(Decimal("0.01"))
        trecho.save(update_fields=["valor_km_aprovado"])


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0008_auditoria_aprovacao"),
    ]

    operations = [
        migrations.RunPython(normalizar_valores_aprovados, migrations.RunPython.noop),
    ]
