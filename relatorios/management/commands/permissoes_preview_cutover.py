from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from relatorios.services.autorizacao_service import (
    GRUPO_ADMIN_ERP,
    GRUPO_DOMAIN_ADMINS,
    GRUPO_FINANCEIRO,
    GRUPO_GESTOR,
    usuario_eh_admin_extra,
    usuario_eh_administrativo,
    usuario_eh_domain_admin,
    usuario_tem_acesso_total,
)
from relatorios.services.permissoes_service import (
    CodigoPermissao,
    comparar_permissao_legado_central,
    usuario_tem_full_access_erp,
)


CODIGOS_PREVIEW = (
    CodigoPermissao.ERP_ACESSAR,
    CodigoPermissao.RELATORIOS_CRIAR,
    CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
    CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
    CodigoPermissao.FINANCEIRO_ACESSAR,
    CodigoPermissao.RELATORIOS_APROVAR,
    CodigoPermissao.RELATORIOS_REJEITAR,
    CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE,
    CodigoPermissao.FINANCEIRO_ALTERAR_VALORES,
    CodigoPermissao.FINANCEIRO_ALTERAR_ITENS,
    CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS,
    CodigoPermissao.RELATORIOS_PDF_INTERNO,
    CodigoPermissao.RELATORIOS_REABRIR,
    CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
    CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM,
    CodigoPermissao.MANUTENCAO_ACESSAR,
    CodigoPermissao.USUARIOS_GERENCIAR,
    CodigoPermissao.PERMISSOES_GERENCIAR,
    CodigoPermissao.AJUDA_EDITAR,
)

GRUPOS_RELEVANTES = {
    GRUPO_DOMAIN_ADMINS,
    GRUPO_FINANCEIRO,
    GRUPO_GESTOR,
    GRUPO_ADMIN_ERP,
}


class Command(BaseCommand):
    help = "Compara permissoes legadas e centralizadas sem alterar dados."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-changes",
            action="store_true",
            help="Exibe apenas usuarios com diferenca entre legado e central.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        usuarios = User.objects.filter(is_active=True).prefetch_related("groups").order_by("username")

        total = 0
        sem_mudanca = 0
        reducao = 0
        aumento = 0
        revisar = 0
        full_central = 0

        for usuario in usuarios:
            total += 1
            grupos = sorted(usuario.groups.values_list("name", flat=True))
            comparacoes = [
                comparar_permissao_legado_central(usuario, codigo)
                for codigo in CODIGOS_PREVIEW
            ]
            perdas = [item["codigo"] for item in comparacoes if item["legado"] and not item["central"]]
            ganhos = [item["codigo"] for item in comparacoes if not item["legado"] and item["central"]]

            if not perdas and not ganhos:
                sem_mudanca += 1
                if options["only_changes"]:
                    continue
            if perdas:
                reducao += 1
            if ganhos:
                aumento += 1

            origem = self._origens(usuario, grupos)
            if usuario_tem_full_access_erp(usuario):
                full_central += 1
            if perdas or ganhos or origem:
                revisar += 1

            self.stdout.write(
                " ".join(
                    [
                        f"usuario={usuario.get_username()}",
                        f"id={usuario.pk}",
                        f"grupos={','.join(grupos) or '-'}",
                        f"origem={','.join(origem) or '-'}",
                        f"full_access_central={int(usuario_tem_full_access_erp(usuario))}",
                        f"perdas={','.join(perdas) or '-'}",
                        f"ganhos={','.join(ganhos) or '-'}",
                    ]
                )
            )

        self.stdout.write("")
        self.stdout.write(f"usuarios_analisados={total}")
        self.stdout.write(f"sem_mudanca={sem_mudanca}")
        self.stdout.write(f"com_reducao_planejada={reducao}")
        self.stdout.write(f"com_aumento={aumento}")
        self.stdout.write(f"requerem_revisao_manual={revisar}")
        self.stdout.write(f"full_access_central={full_central}")

    def _origens(self, usuario, grupos):
        origem = []
        grupos_set = set(grupos)
        for grupo in sorted(GRUPOS_RELEVANTES.intersection(grupos_set)):
            origem.append(grupo.replace(" ", "_"))
        grupos_socios = [
            grupo for grupo in grupos if "socio" in grupo.lower() or "sócio" in grupo.lower()
        ]
        origem.extend(grupo.replace(" ", "_") for grupo in grupos_socios)
        if getattr(usuario, "is_superuser", False):
            origem.append("is_superuser")
        if getattr(usuario, "is_staff", False):
            origem.append("is_staff")
        if usuario_eh_admin_extra(usuario):
            origem.append("EXTRA_ADMIN_USERS")
        if usuario_eh_domain_admin(usuario):
            origem.append("domain_admin_legacy")
        if usuario_tem_acesso_total(usuario):
            origem.append("acesso_total_legacy")
        if usuario_eh_administrativo(usuario):
            origem.append("administrativo_legacy")
        return origem
