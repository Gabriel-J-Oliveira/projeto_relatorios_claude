from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("relatorios", "0060_empresa_grupo_casa_chico_pneus"),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricoPermissaoUsuario",
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
                ("codigo", models.CharField(max_length=100, verbose_name="Código da permissão")),
                (
                    "estado_anterior",
                    models.CharField(
                        choices=[
                            ("herdar", "Herdar"),
                            ("permitir", "Permitir"),
                            ("negar", "Negar"),
                        ],
                        max_length=10,
                        verbose_name="Estado anterior",
                    ),
                ),
                (
                    "estado_novo",
                    models.CharField(
                        choices=[
                            ("herdar", "Herdar"),
                            ("permitir", "Permitir"),
                            ("negar", "Negar"),
                        ],
                        max_length=10,
                        verbose_name="Estado novo",
                    ),
                ),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "alterado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="historico_permissoes_realizadas",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Alterado por",
                    ),
                ),
                (
                    "usuario_afetado",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="historico_permissoes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Usuário afetado",
                    ),
                ),
            ],
            options={
                "verbose_name": "Histórico de permissão do usuário",
                "verbose_name_plural": "Histórico de permissões dos usuários",
                "ordering": ["-criado_em"],
            },
        ),
        migrations.CreateModel(
            name="PermissaoUsuarioOverride",
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
                ("codigo", models.CharField(max_length=100, verbose_name="Código da permissão")),
                (
                    "estado",
                    models.CharField(
                        choices=[("permitir", "Permitir"), ("negar", "Negar")],
                        max_length=10,
                        verbose_name="Estado",
                    ),
                ),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "atualizado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="permissoes_overrides_atualizadas",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Atualizado por",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="permissoes_overrides",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Usuário",
                    ),
                ),
            ],
            options={
                "verbose_name": "Override de permissão do usuário",
                "verbose_name_plural": "Overrides de permissões dos usuários",
                "ordering": ["usuario__username", "codigo"],
            },
        ),
        migrations.AddIndex(
            model_name="historicopermissaousuario",
            index=models.Index(
                fields=["usuario_afetado", "codigo"],
                name="relatorios__hist_perm_user_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="historicopermissaousuario",
            index=models.Index(
                fields=["codigo", "criado_em"],
                name="relatorios__hist_perm_cod_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="permissaousuariooverride",
            index=models.Index(fields=["codigo"], name="relatorios__perm_codigo_idx"),
        ),
        migrations.AddIndex(
            model_name="permissaousuariooverride",
            index=models.Index(
                fields=["usuario", "estado"],
                name="relatorios__perm_usr_est_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="permissaousuariooverride",
            constraint=models.UniqueConstraint(
                fields=("usuario", "codigo"),
                name="uniq_permissao_usuario_codigo",
            ),
        ),
    ]
