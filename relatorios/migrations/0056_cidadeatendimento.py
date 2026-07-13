from django.db import migrations, models
import django.db.models.deletion


def criar_cidades_legadas(apps, schema_editor):
    RelatorioTecnico = apps.get_model("relatorios", "RelatorioTecnico")
    CidadeAtendimento = apps.get_model("relatorios", "CidadeAtendimento")

    for relatorio in RelatorioTecnico.objects.all().iterator():
        if CidadeAtendimento.objects.filter(relatorio_id=relatorio.pk).exists():
            continue
        cidade = (relatorio.cidade_atendimento or "").strip()
        if not cidade:
            continue
        CidadeAtendimento.objects.create(
            relatorio_id=relatorio.pk,
            municipio_id=relatorio.municipio_atendimento_id,
            cidade=cidade,
            uf=(relatorio.uf_atendimento or "").strip().upper(),
            tipo_localidade=relatorio.tipo_localidade or "",
            ordem=0,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0055_despesatecnico"),
    ]

    operations = [
        migrations.CreateModel(
            name="CidadeAtendimento",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cidade", models.CharField(max_length=120, verbose_name="Cidade")),
                (
                    "uf",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("AC", "Acre"),
                            ("AL", "Alagoas"),
                            ("AP", "Amapá"),
                            ("AM", "Amazonas"),
                            ("BA", "Bahia"),
                            ("CE", "Ceará"),
                            ("DF", "Distrito Federal"),
                            ("ES", "Espírito Santo"),
                            ("GO", "Goiás"),
                            ("MA", "Maranhão"),
                            ("MT", "Mato Grosso"),
                            ("MS", "Mato Grosso do Sul"),
                            ("MG", "Minas Gerais"),
                            ("PA", "Pará"),
                            ("PB", "Paraíba"),
                            ("PR", "Paraná"),
                            ("PE", "Pernambuco"),
                            ("PI", "Piauí"),
                            ("RJ", "Rio de Janeiro"),
                            ("RN", "Rio Grande do Norte"),
                            ("RS", "Rio Grande do Sul"),
                            ("RO", "Rondônia"),
                            ("RR", "Roraima"),
                            ("SC", "Santa Catarina"),
                            ("SP", "São Paulo"),
                            ("SE", "Sergipe"),
                            ("TO", "Tocantins"),
                        ],
                        max_length=2,
                        verbose_name="UF",
                    ),
                ),
                (
                    "tipo_localidade",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("capital", "Capital"),
                            ("interior", "Interior"),
                            ("fronteira", "Fronteira"),
                        ],
                        max_length=12,
                        verbose_name="Localidade",
                    ),
                ),
                ("endereco", models.CharField(blank=True, max_length=255, verbose_name="Endereço")),
                ("ordem", models.PositiveIntegerField(default=0, verbose_name="Ordem")),
                ("observacao", models.CharField(blank=True, max_length=255, verbose_name="Observação")),
                (
                    "municipio",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cidades_atendimento",
                        to="relatorios.municipio",
                        verbose_name="Município",
                    ),
                ),
                (
                    "relatorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cidades_atendimento",
                        to="relatorios.relatoriotecnico",
                        verbose_name="Relatório",
                    ),
                ),
            ],
            options={
                "verbose_name": "Cidade de atendimento",
                "verbose_name_plural": "Cidades de atendimento",
                "ordering": ["ordem", "pk"],
            },
        ),
        migrations.AddIndex(
            model_name="cidadeatendimento",
            index=models.Index(fields=["relatorio", "ordem"], name="relatorios__relator_4caa1d_idx"),
        ),
        migrations.AddIndex(
            model_name="cidadeatendimento",
            index=models.Index(fields=["cidade", "uf"], name="relatorios__cidade_25b8d7_idx"),
        ),
        migrations.RunPython(criar_cidades_legadas, noop_reverse),
    ]
