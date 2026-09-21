from django.db import migrations, models
import django.db.models.deletion
import unicodedata


def _normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.lower().strip().split())


def _associar_unico(empresa_grupo_cliente_model, empresa_grupo, clientes):
    clientes = list(clientes)
    if len(clientes) != 1:
        return
    empresa_grupo_cliente_model.objects.get_or_create(
        empresa_grupo=empresa_grupo,
        defaults={"cliente_id": clientes[0].pk},
    )


def _associar_blazius(empresa_grupo_cliente_model, cliente_model):
    cnpj = "24649215000104"
    clientes = list(cliente_model.objects.filter(cnpj_cpf=cnpj))
    ativos = [cliente for cliente in clientes if cliente.ativo]

    if len(ativos) == 1:
        cliente = ativos[0]
    elif not clientes:
        cliente = cliente_model.objects.create(
            nome="BLAZIUS & LORENZETTI ADVOGADOS ASSOCIADOS",
            razao_social="BLAZIUS & LORENZETTI ADVOGADOS ASSOCIADOS",
            cnpj_cpf=cnpj,
            ativo=True,
            origem_api=False,
        )
    else:
        return

    empresa_grupo_cliente_model.objects.get_or_create(
        empresa_grupo="blazius_e_lorenzetti",
        defaults={"cliente_id": cliente.pk},
    )


def criar_associacoes_seguras(apps, schema_editor):
    Cliente = apps.get_model("relatorios", "Cliente")
    EmpresaGrupoCliente = apps.get_model("relatorios", "EmpresaGrupoCliente")

    _associar_blazius(EmpresaGrupoCliente, Cliente)
    _associar_unico(
        EmpresaGrupoCliente,
        "controlsul",
        Cliente.objects.filter(ativo=True, cnpj_cpf="04672088000149"),
    )
    _associar_unico(
        EmpresaGrupoCliente,
        "casa_chico_de_pneus",
        Cliente.objects.filter(
            ativo=True,
            origem_api=True,
            cnpj_cpf="77816478000119",
        ),
    )

    fiscais = [
        cliente
        for cliente in Cliente.objects.filter(ativo=True)
        if "fiscalmax"
        in {
            _normalizar(cliente.nome),
            _normalizar(cliente.razao_social),
            _normalizar(cliente.nome_fantasia),
        }
    ]
    _associar_unico(EmpresaGrupoCliente, "fiscalmax", fiscais)


def remover_associacoes_seguras(apps, schema_editor):
    EmpresaGrupoCliente = apps.get_model("relatorios", "EmpresaGrupoCliente")
    EmpresaGrupoCliente.objects.filter(
        empresa_grupo__in=[
            "blazius_e_lorenzetti",
            "controlsul",
            "casa_chico_de_pneus",
            "fiscalmax",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("relatorios", "0061_permissoes_usuario_override_historico"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmpresaGrupoCliente",
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
                            ("casa_chico_de_pneus", "CASA CHICO DE PNEUS"),
                        ],
                        max_length=30,
                        unique=True,
                        verbose_name="Empresa do grupo",
                    ),
                ),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "cliente",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="empresa_grupo_canonica",
                        to="relatorios.cliente",
                        verbose_name="Cliente canonico",
                    ),
                ),
            ],
            options={
                "verbose_name": "Cliente canonico da empresa do grupo",
                "verbose_name_plural": "Clientes canonicos das empresas do grupo",
                "ordering": ["empresa_grupo"],
            },
        ),
        migrations.RunPython(criar_associacoes_seguras, remover_associacoes_seguras),
    ]
