from dataclasses import dataclass
import logging
from typing import Callable

from django.conf import settings
from django.db import transaction

from relatorios.models import (
    EstadoHistoricoPermissao,
    EstadoPermissaoUsuario,
    StatusRelatorio,
)
from relatorios.services.autorizacao_service import (
    _normalizar_login_usuario,
    status_permite_edicao_relatorio_alheio_autorizado,
    status_permite_edicao_relatorio_proprio,
    status_permite_envio_relatorio,
    usuario_eh_administrativo,
    usuario_pode_aprovar_relatorio_legado,
    usuario_pode_acessar_erp,
    usuario_pode_acessar_manutencao_legado,
    usuario_pode_atuar_como_financeiro,
    usuario_pode_devolver_relatorio_ajuste_legado,
    usuario_pode_editar_relatorio_legado,
    usuario_pode_enviar_relatorio_legado,
    usuario_pode_rejeitar_relatorio_legado,
    usuario_pode_reabrir_relatorio,
    usuario_pode_visualizar_relatorio_legado,
)
from relatorios.services.clientes_valor_km_service import (
    usuario_pode_configurar_valor_km_legado,
)
from relatorios.services.help_center_service import usuario_pode_editar_ajuda_legado


logger = logging.getLogger(__name__)


class CategoriaPermissao:
    RELATORIOS = "relatorios"
    FINANCEIRO = "financeiro"
    CADASTROS = "cadastros"
    MANUTENCAO = "manutencao"
    ADMINISTRACAO = "administracao"
    AJUDA = "ajuda"
    SISTEMA = "sistema"


class EscopoPermissao:
    GLOBAL = "global"
    OBJETO = "objeto"
    OBJETO_STATUS = "objeto_status"
    EXTERNO = "externo"


class SensibilidadePermissao:
    NORMAL = "normal"
    SENSIVEL = "sensivel"
    CRITICA = "critica"


class CodigoPermissao:
    ERP_ACESSAR = "erp.acessar"
    DASHBOARD_GLOBAL = "dashboard.global"

    RELATORIOS_CRIAR = "relatorios.criar"
    RELATORIOS_VISUALIZAR = "relatorios.visualizar"
    RELATORIOS_VISUALIZAR_ALHEIOS = "relatorios.visualizar_alheios"
    RELATORIOS_EDITAR = "relatorios.editar"
    RELATORIOS_EDITAR_ALHEIOS = "relatorios.editar_alheios"
    RELATORIOS_ENVIAR = "relatorios.enviar"
    RELATORIOS_DUPLICAR = "relatorios.duplicar"
    RELATORIOS_EXCLUIR_RASCUNHO = "relatorios.excluir_rascunho"
    RELATORIOS_APROVAR = "relatorios.aprovar"
    RELATORIOS_REJEITAR = "relatorios.rejeitar"
    RELATORIOS_DEVOLVER_AJUSTE = "relatorios.devolver_ajuste"
    RELATORIOS_MUDAR_STATUS_FINANCEIRO = "relatorios.mudar_status_financeiro"
    RELATORIOS_ALTERAR_STATUS_ADMIN = "relatorios.alterar_status_administrativo"
    RELATORIOS_REABRIR = "relatorios.reabrir"
    RELATORIOS_PDF_CLIENTE = "relatorios.pdf_cliente"
    RELATORIOS_PDF_INTERNO = "relatorios.pdf_interno"

    FINANCEIRO_ACESSAR = "financeiro.acessar"
    FINANCEIRO_ATUAR = "financeiro.atuar"
    FINANCEIRO_ALTERAR_VALORES = "financeiro.alterar_valores"
    FINANCEIRO_ALTERAR_RATEIOS = "financeiro.alterar_rateios"
    FINANCEIRO_ALTERAR_ITENS = "financeiro.alterar_itens"

    CADASTROS_CLIENTES_GERENCIAR = "cadastros.clientes.gerenciar"
    CADASTROS_TECNICOS_GERENCIAR = "cadastros.tecnicos.gerenciar"
    CADASTROS_ADIANTAMENTOS_GERENCIAR = "cadastros.adiantamentos.gerenciar"
    CADASTROS_GERENCIAR = "cadastros.gerenciar"
    CLIENTES_CONFIGURAR_VALOR_KM = "clientes.configurar_valor_km"

    MANUTENCAO_ACESSAR = "manutencao.acessar"
    MANUTENCAO_POLITICAS = "manutencao.politicas"
    MANUTENCAO_EMAILS = "manutencao.emails"

    USUARIOS_GERENCIAR = "usuarios.gerenciar"
    PERMISSOES_GERENCIAR = "permissoes.gerenciar"

    AJUDA_EDITAR = "ajuda.editar"


Evaluator = Callable[[object, object | None], bool]


@dataclass(frozen=True)
class PermissaoTecnica:
    codigo: str
    nome: str
    descricao: str
    categoria: str
    escopo: str
    evaluator_legado: Evaluator
    objeto_obrigatorio: bool = False
    regra_legada: str = ""
    sensibilidade: str = SensibilidadePermissao.NORMAL


def _global(funcao):
    return lambda usuario, objeto=None: bool(funcao(usuario))


def _relatorio_legado(funcao):
    def evaluator(usuario, objeto=None):
        if objeto is None:
            return False
        return bool(funcao(usuario, objeto))

    return evaluator


def _financeiro_legado(usuario, objeto=None):
    return bool(usuario_pode_atuar_como_financeiro(usuario))


def _manutencao_legado(usuario, objeto=None):
    return bool(usuario_pode_acessar_manutencao_legado(usuario))


def _catalogo():
    return {
        CodigoPermissao.ERP_ACESSAR: PermissaoTecnica(
            CodigoPermissao.ERP_ACESSAR,
            "Acessar ERP",
            "Permite acessar o sistema de relatorios.",
            CategoriaPermissao.SISTEMA,
            EscopoPermissao.GLOBAL,
            _global(usuario_pode_acessar_erp),
            regra_legada="usuario_pode_acessar_erp",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.DASHBOARD_GLOBAL: PermissaoTecnica(
            CodigoPermissao.DASHBOARD_GLOBAL,
            "Ver dashboard global",
            "Permite visualizar dados globais no dashboard.",
            CategoriaPermissao.SISTEMA,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_CRIAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_CRIAR,
            "Criar relatorio",
            "Permite iniciar um novo relatorio tecnico.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.GLOBAL,
            _global(usuario_pode_acessar_erp),
            regra_legada="usuario_pode_acessar_erp",
        ),
        CodigoPermissao.RELATORIOS_VISUALIZAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_VISUALIZAR,
            "Visualizar relatorio",
            "Permite visualizar um relatorio conforme regra por objeto.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO,
            _relatorio_legado(usuario_pode_visualizar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_visualizar_relatorio",
        ),
        CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            "Visualizacao Universal",
            "Permite visualizar relatorios criados por outros usuarios.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_EDITAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_EDITAR,
            "Editar relatorio",
            "Permite editar um relatorio conforme usuario, objeto e status.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_editar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_editar_relatorio",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
            "Editar relatorios de outros usuarios",
            "Permite editar relatorios criados por outros usuarios sem ignorar workflow.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_ENVIAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_ENVIAR,
            "Enviar relatorio",
            "Permite enviar um relatorio para conferencia conforme usuario e status.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_enviar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_enviar_relatorio",
        ),
        CodigoPermissao.RELATORIOS_DUPLICAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_DUPLICAR,
            "Duplicar relatorio",
            "Permite duplicar relatorio visivel quando a regra do objeto permitir.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_visualizar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="relatorio visivel",
        ),
        CodigoPermissao.RELATORIOS_EXCLUIR_RASCUNHO: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_EXCLUIR_RASCUNHO,
            "Excluir rascunho",
            "Permite excluir rascunho quando o workflow permitir.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_editar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_editar_relatorio + status rascunho",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.FINANCEIRO_ACESSAR: PermissaoTecnica(
            CodigoPermissao.FINANCEIRO_ACESSAR,
            "Acessar financeiro",
            "Permite acessar a area de analise financeira sem conceder acoes.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.GLOBAL,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.FINANCEIRO_ATUAR: PermissaoTecnica(
            CodigoPermissao.FINANCEIRO_ATUAR,
            "Atuar no financeiro",
            "Compatibilidade tecnica com a regra financeira legada agrupada.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.GLOBAL,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_APROVAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_APROVAR,
            "Aprovar relatorio",
            "Permite aprovar relatorios quando o workflow permitir.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_aprovar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_aprovar_relatorio_legado",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.RELATORIOS_REJEITAR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_REJEITAR,
            "Rejeitar relatorio",
            "Permite rejeitar relatorios quando o workflow permitir.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_rejeitar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_rejeitar_relatorio_legado",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE,
            "Devolver para ajuste",
            "Permite devolver relatorios para ajuste quando o workflow permitir.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_devolver_relatorio_ajuste_legado),
            objeto_obrigatorio=True,
            regra_legada="usuario_pode_devolver_relatorio_ajuste_legado",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_MUDAR_STATUS_FINANCEIRO: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_MUDAR_STATUS_FINANCEIRO,
            "Alterar status financeiro",
            "Compatibilidade com mudancas financeiras de status do fluxo atual.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.RELATORIOS_ALTERAR_STATUS_ADMIN: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_ALTERAR_STATUS_ADMIN,
            "Alterar status administrativamente",
            "Capacidade futura critica para alteracao administrativa de status.",
            CategoriaPermissao.ADMINISTRACAO,
            EscopoPermissao.OBJETO_STATUS,
            lambda usuario, objeto=None: False,
            regra_legada="sem fluxo legado geral",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.RELATORIOS_REABRIR: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_REABRIR,
            "Reabrir relatorio",
            "Permite reabrir relatorios aprovados conforme regra administrativa atual.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _global(usuario_pode_reabrir_relatorio),
            regra_legada="usuario_pode_reabrir_relatorio",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.RELATORIOS_PDF_CLIENTE: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_PDF_CLIENTE,
            "Gerar PDF do cliente",
            "Permite gerar PDF/ZIP de cliente quando o workflow permitir.",
            CategoriaPermissao.RELATORIOS,
            EscopoPermissao.OBJETO_STATUS,
            _relatorio_legado(usuario_pode_visualizar_relatorio_legado),
            objeto_obrigatorio=True,
            regra_legada="relatorio visivel + aprovado",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.RELATORIOS_PDF_INTERNO: PermissaoTecnica(
            CodigoPermissao.RELATORIOS_PDF_INTERNO,
            "Gerar PDF interno",
            "Permite gerar PDF interno financeiro.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.FINANCEIRO_ALTERAR_VALORES: PermissaoTecnica(
            CodigoPermissao.FINANCEIRO_ALTERAR_VALORES,
            "Alterar valores aprovados",
            "Permite alterar valores aprovados quando o workflow permitir.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS: PermissaoTecnica(
            CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS,
            "Alterar rateios",
            "Permite alterar rateios financeiros quando o workflow permitir.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.FINANCEIRO_ALTERAR_ITENS: PermissaoTecnica(
            CodigoPermissao.FINANCEIRO_ALTERAR_ITENS,
            "Rejeitar ou restaurar itens",
            "Permite rejeitar/restaurar despesas e trechos na conferencia financeira.",
            CategoriaPermissao.FINANCEIRO,
            EscopoPermissao.OBJETO_STATUS,
            _financeiro_legado,
            regra_legada="usuario_pode_atuar_como_financeiro",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
            "Editar clientes",
            "Permite acessar, criar, editar e excluir clientes.",
            CategoriaPermissao.CADASTROS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.CADASTROS_TECNICOS_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.CADASTROS_TECNICOS_GERENCIAR,
            "Gerenciar tecnicos",
            "Capacidade futura para tecnicos locais; hoje a tela e essencialmente AD/readonly.",
            CategoriaPermissao.CADASTROS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.CADASTROS_ADIANTAMENTOS_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.CADASTROS_ADIANTAMENTOS_GERENCIAR,
            "Gerenciar adiantamentos",
            "Permite acessar, criar, editar e excluir adiantamentos.",
            CategoriaPermissao.CADASTROS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.CADASTROS_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.CADASTROS_GERENCIAR,
            "Gerenciar cadastros",
            "Compatibilidade com a regra administrativa efetiva das views de cadastros.",
            CategoriaPermissao.CADASTROS,
            EscopoPermissao.GLOBAL,
            _global(usuario_eh_administrativo),
            regra_legada="usuario_eh_administrativo",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM: PermissaoTecnica(
            CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM,
            "Configurar valor KM de clientes",
            "Permite preencher ou alterar valor de KM em clientes.",
            CategoriaPermissao.CADASTROS,
            EscopoPermissao.GLOBAL,
            _global(usuario_pode_configurar_valor_km_legado),
            regra_legada="usuario_pode_configurar_valor_km",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.MANUTENCAO_ACESSAR: PermissaoTecnica(
            CodigoPermissao.MANUTENCAO_ACESSAR,
            "Acessar manutencao",
            "Permite acessar a Central de Manutencao.",
            CategoriaPermissao.MANUTENCAO,
            EscopoPermissao.GLOBAL,
            _manutencao_legado,
            regra_legada="usuario_pode_acessar_manutencao",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.MANUTENCAO_POLITICAS: PermissaoTecnica(
            CodigoPermissao.MANUTENCAO_POLITICAS,
            "Gerenciar politicas",
            "Codigo tecnico futuro para politicas; V1 usa manutencao.acessar.",
            CategoriaPermissao.MANUTENCAO,
            EscopoPermissao.GLOBAL,
            _manutencao_legado,
            regra_legada="usuario_pode_acessar_manutencao",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.MANUTENCAO_EMAILS: PermissaoTecnica(
            CodigoPermissao.MANUTENCAO_EMAILS,
            "Gerenciar emails",
            "Codigo tecnico futuro para emails; V1 usa manutencao.acessar.",
            CategoriaPermissao.MANUTENCAO,
            EscopoPermissao.GLOBAL,
            _manutencao_legado,
            regra_legada="usuario_pode_acessar_manutencao",
            sensibilidade=SensibilidadePermissao.SENSIVEL,
        ),
        CodigoPermissao.USUARIOS_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.USUARIOS_GERENCIAR,
            "Gerenciar usuarios",
            "Permite administrar usuarios na futura Central.",
            CategoriaPermissao.ADMINISTRACAO,
            EscopoPermissao.GLOBAL,
            lambda usuario, objeto=None: False,
            regra_legada="sem tela legada",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.PERMISSOES_GERENCIAR: PermissaoTecnica(
            CodigoPermissao.PERMISSOES_GERENCIAR,
            "Gerenciar permissoes",
            "Permite alterar permissoes de usuarios na futura Central.",
            CategoriaPermissao.ADMINISTRACAO,
            EscopoPermissao.GLOBAL,
            lambda usuario, objeto=None: False,
            regra_legada="sem tela legada",
            sensibilidade=SensibilidadePermissao.CRITICA,
        ),
        CodigoPermissao.AJUDA_EDITAR: PermissaoTecnica(
            CodigoPermissao.AJUDA_EDITAR,
            "Editar portal de documentacao",
            "Permite criar, editar, excluir e enviar imagens da central de ajuda.",
            CategoriaPermissao.AJUDA,
            EscopoPermissao.GLOBAL,
            _global(usuario_pode_editar_ajuda_legado),
            regra_legada="usuario_pode_editar_ajuda",
        ),
    }


PERMISSOES = _catalogo()


def obter_permissao(codigo):
    return PERMISSOES.get(str(codigo or ""))


def listar_permissoes():
    return tuple(PERMISSOES.values())


def permissoes_central_ativa():
    return bool(getattr(settings, "PERMISSOES_CENTRAL_ENABLED", False))


def _identidades_usuario(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    candidatos = {
        getattr(user, "username", ""),
        user.get_username() if hasattr(user, "get_username") else "",
        getattr(user, "email", ""),
    }
    return {_normalizar_login_usuario(valor) for valor in candidatos if _normalizar_login_usuario(valor)}


def _full_access_configurado():
    valor = getattr(settings, "ERP_FULL_ACCESS_USERS", [])
    if isinstance(valor, str):
        bruto = valor.split(",")
    else:
        bruto = valor or []
    return {_normalizar_login_usuario(item) for item in bruto if _normalizar_login_usuario(item)}


def usuario_tem_full_access_erp(usuario):
    if not getattr(usuario, "is_authenticated", False):
        return False
    full_users = _full_access_configurado()
    if not full_users:
        return False
    return bool(full_users.intersection(_identidades_usuario(usuario)))


def _estado_override(usuario, codigo):
    if not getattr(usuario, "is_authenticated", False):
        return None
    from relatorios.models import PermissaoUsuarioOverride

    override = (
        PermissaoUsuarioOverride.objects.filter(usuario=usuario, codigo=codigo)
        .only("estado")
        .first()
    )
    return override.estado if override else None


def estado_efetivo_override(usuario, codigo):
    return _estado_override(usuario, codigo) or EstadoHistoricoPermissao.HERDAR


def _override_permite(usuario, codigo):
    return _estado_override(usuario, codigo) == EstadoPermissaoUsuario.PERMITIR


def _override_nega(usuario, codigo):
    return _estado_override(usuario, codigo) == EstadoPermissaoUsuario.NEGAR


def _usuario_eh_dono(usuario, relatorio):
    return bool(
        getattr(usuario, "is_authenticated", False)
        and getattr(relatorio, "criado_por_id", None) == getattr(usuario, "pk", None)
    )


def _status_relatorio(relatorio):
    return getattr(relatorio, "status", None)


def _relatorio_finalizado(relatorio):
    return _status_relatorio(relatorio) in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}


def _relatorio_visivel_central(usuario, relatorio):
    if relatorio is None:
        return False
    if usuario_tem_full_access_erp(usuario):
        return True
    if _usuario_eh_dono(usuario, relatorio):
        return True
    return _capacidade_global_central(usuario, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)


def _capacidade_global_central(usuario, codigo):
    if not getattr(usuario, "is_authenticated", False):
        return False
    if usuario_tem_full_access_erp(usuario):
        return True
    if _override_nega(usuario, codigo):
        return False
    if _override_permite(usuario, codigo):
        return True
    if codigo == CodigoPermissao.ERP_ACESSAR:
        return usuario_pode_acessar_erp(usuario)
    if codigo == CodigoPermissao.RELATORIOS_CRIAR:
        return usuario_pode_acessar_erp(usuario)
    return False


def _financeiro_central(usuario, codigo, relatorio=None):
    if not _capacidade_global_central(usuario, codigo):
        return False
    if relatorio is None:
        return True
    if not _relatorio_visivel_central(usuario, relatorio):
        return False
    return True


def _usuario_tem_permissao_central_impl(usuario, codigo, objeto=None):
    permissao = obter_permissao(codigo)
    if permissao is None:
        return False
    if not getattr(usuario, "is_authenticated", False):
        return False

    if codigo == CodigoPermissao.RELATORIOS_VISUALIZAR:
        return _relatorio_visivel_central(usuario, objeto)
    if codigo == CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS:
        return _capacidade_global_central(usuario, codigo)
    if codigo == CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS:
        return (
            _capacidade_global_central(usuario, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
            and _capacidade_global_central(usuario, codigo)
        )
    if codigo == CodigoPermissao.RELATORIOS_EDITAR:
        if objeto is None or _relatorio_finalizado(objeto):
            return False
        if usuario_tem_full_access_erp(usuario):
            return status_permite_edicao_relatorio_alheio_autorizado(
                _status_relatorio(objeto)
            )
        if _usuario_eh_dono(usuario, objeto):
            return status_permite_edicao_relatorio_proprio(_status_relatorio(objeto))
        return (
            status_permite_edicao_relatorio_alheio_autorizado(_status_relatorio(objeto))
            and _usuario_tem_permissao_central_impl(
                usuario, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS
            )
        )
    if codigo == CodigoPermissao.RELATORIOS_ENVIAR:
        if objeto is None or not status_permite_envio_relatorio(_status_relatorio(objeto)):
            return False
        if usuario_tem_full_access_erp(usuario):
            return True
        if _usuario_eh_dono(usuario, objeto):
            return True
        return _usuario_tem_permissao_central_impl(
            usuario, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS
        )
    if codigo == CodigoPermissao.RELATORIOS_DUPLICAR:
        if objeto is None or _status_relatorio(objeto) == StatusRelatorio.AJUSTE:
            return False
        return _relatorio_visivel_central(usuario, objeto)
    if codigo == CodigoPermissao.RELATORIOS_EXCLUIR_RASCUNHO:
        if objeto is None or _status_relatorio(objeto) != StatusRelatorio.RASCUNHO:
            return False
        if usuario_tem_full_access_erp(usuario):
            return True
        if _usuario_eh_dono(usuario, objeto):
            return True
        return _usuario_tem_permissao_central_impl(
            usuario, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS
        )
    if codigo == CodigoPermissao.RELATORIOS_PDF_CLIENTE:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) == StatusRelatorio.APROVADO
            and _relatorio_visivel_central(usuario, objeto)
        )
    if codigo == CodigoPermissao.FINANCEIRO_ACESSAR:
        return _capacidade_global_central(usuario, codigo)
    if codigo == CodigoPermissao.RELATORIOS_APROVAR:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) == StatusRelatorio.CONFERENCIA
            and _financeiro_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR, objeto)
            and _financeiro_central(usuario, codigo, objeto)
        )
    if codigo == CodigoPermissao.RELATORIOS_REJEITAR:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) == StatusRelatorio.CONFERENCIA
            and _financeiro_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR, objeto)
            and _financeiro_central(usuario, codigo, objeto)
        )
    if codigo == CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) == StatusRelatorio.CONFERENCIA
            and _financeiro_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR, objeto)
            and _financeiro_central(usuario, codigo, objeto)
        )
    if codigo in {
        CodigoPermissao.FINANCEIRO_ALTERAR_VALORES,
        CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS,
        CodigoPermissao.FINANCEIRO_ALTERAR_ITENS,
    }:
        return bool(
            objeto is not None
            and not _relatorio_finalizado(objeto)
            and _financeiro_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR, objeto)
            and _financeiro_central(usuario, codigo, objeto)
        )
    if codigo == CodigoPermissao.RELATORIOS_PDF_INTERNO:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) in {StatusRelatorio.APROVADO, StatusRelatorio.REJEITADO}
            and _financeiro_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR, objeto)
            and _financeiro_central(usuario, codigo, objeto)
        )
    if codigo == CodigoPermissao.RELATORIOS_REABRIR:
        return bool(
            objeto is not None
            and _status_relatorio(objeto) == StatusRelatorio.APROVADO
            and _relatorio_visivel_central(usuario, objeto)
            and _capacidade_global_central(usuario, codigo)
        )
    if codigo in {
        CodigoPermissao.RELATORIOS_MUDAR_STATUS_FINANCEIRO,
        CodigoPermissao.RELATORIOS_ALTERAR_STATUS_ADMIN,
    }:
        return bool(
            objeto is not None
            and _relatorio_visivel_central(usuario, objeto)
            and _capacidade_global_central(usuario, codigo)
        )
    return _capacidade_global_central(usuario, codigo)


def usuario_tem_permissao_legada(usuario, codigo, objeto=None):
    permissao = obter_permissao(codigo)
    if permissao is None:
        return False
    if permissao.objeto_obrigatorio and objeto is None:
        return False
    return bool(permissao.evaluator_legado(usuario, objeto))


def usuario_tem_permissao_central(usuario, codigo, objeto=None):
    permissao = obter_permissao(codigo)
    if permissao is None:
        return False
    if permissao.objeto_obrigatorio and objeto is None:
        return False
    return bool(_usuario_tem_permissao_central_impl(usuario, permissao.codigo, objeto))


def usuario_tem_permissao(usuario, codigo, objeto=None):
    if permissoes_central_ativa():
        return usuario_tem_permissao_central(usuario, codigo, objeto=objeto)
    return usuario_tem_permissao_legada(usuario, codigo, objeto=objeto)


def comparar_permissao_legado_central(usuario, codigo, objeto=None):
    return {
        "codigo": codigo,
        "legado": usuario_tem_permissao_legada(usuario, codigo, objeto=objeto),
        "central": usuario_tem_permissao_central(usuario, codigo, objeto=objeto),
    }


def avaliar_permissao_cutover(usuario, codigo, legado, objeto=None):
    legado_result = bool(legado() if callable(legado) else legado)
    central_ativa = permissoes_central_ativa()
    object_name = objeto.__class__.__name__ if objeto is not None else ""
    object_id = getattr(objeto, "pk", None)
    object_status = getattr(objeto, "status", "")
    try:
        central_result = usuario_tem_permissao_central(usuario, codigo, objeto=objeto)
    except Exception as exc:
        if central_ativa:
            raise
        logger.warning(
            "[PERMISSOES_SHADOW] code=%s user_id=%s object=%s object_id=%s status=%s legacy=%s central_error=%s",
            codigo,
            getattr(usuario, "pk", None),
            object_name,
            object_id,
            object_status,
            int(legado_result),
            exc.__class__.__name__,
        )
        return legado_result
    if not central_ativa:
        if legado_result != central_result:
            logger.info(
                "[PERMISSOES_SHADOW] code=%s user_id=%s object=%s object_id=%s status=%s legacy=%s central=%s",
                codigo,
                getattr(usuario, "pk", None),
                object_name,
                object_id,
                object_status,
                int(legado_result),
                int(central_result),
            )
        return legado_result
    return central_result


def _estado_historico_de_override(override):
    return override.estado if override else EstadoHistoricoPermissao.HERDAR


def definir_override_permissao(usuario, codigo, estado, alterado_por=None):
    permissao = obter_permissao(codigo)
    if permissao is None:
        raise ValueError("Código de permissão não cadastrado no registry.")
    if estado not in {
        None,
        "",
        EstadoHistoricoPermissao.HERDAR,
        EstadoPermissaoUsuario.PERMITIR,
        EstadoPermissaoUsuario.NEGAR,
    }:
        raise ValueError("Estado de permissão inválido.")

    from relatorios.models import HistoricoPermissaoUsuario, PermissaoUsuarioOverride

    with transaction.atomic():
        override = (
            PermissaoUsuarioOverride.objects.select_for_update()
            .filter(usuario=usuario, codigo=permissao.codigo)
            .first()
        )
        estado_anterior = _estado_historico_de_override(override)
        if estado in {None, "", EstadoHistoricoPermissao.HERDAR}:
            estado_novo = EstadoHistoricoPermissao.HERDAR
            if override:
                override.delete()
        else:
            estado_novo = estado
            if override:
                override.estado = estado
                override.atualizado_por = alterado_por
                override.save(update_fields=["estado", "atualizado_por", "atualizado_em"])
            else:
                PermissaoUsuarioOverride.objects.create(
                    usuario=usuario,
                    codigo=permissao.codigo,
                    estado=estado,
                    atualizado_por=alterado_por,
                )
        HistoricoPermissaoUsuario.objects.create(
            usuario_afetado=usuario,
            codigo=permissao.codigo,
            estado_anterior=estado_anterior,
            estado_novo=estado_novo,
            alterado_por=alterado_por,
        )


def usuario_pode_acessar_central_permissoes(usuario):
    if usuario_tem_full_access_erp(usuario):
        return True
    return bool(
        permissoes_central_ativa()
        and usuario_tem_permissao_central(usuario, CodigoPermissao.PERMISSOES_GERENCIAR)
    )
