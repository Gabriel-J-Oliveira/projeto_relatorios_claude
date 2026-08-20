import json
from decimal import Decimal
from datetime import date
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client as DjangoClient
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Adiantamento,
    AnexoRelatorio,
    Cliente,
    EmailLog,
    EmpresaGrupo,
    EscopoPoliticaValor,
    DespesaCliente,
    DespesaRateio,
    EstadoHistoricoPermissao,
    EstadoPermissaoUsuario,
    HistoricoRelatorio,
    HistoricoPermissaoUsuario,
    ItemDespesa,
    DespesaTecnico,
    QuemPagou,
    PermissaoUsuarioOverride,
    PerfilUsuario,
    PoliticaValor,
    PoliticaValorEmpresaGrupo,
    RelatorioAutoSave,
    RelatorioCliente,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusRelatorio,
    Tecnico,
    TipoAdiantamento,
    TipoDespesa,
    TipoEventoHistorico,
    TipoReembolso,
    TrechoKm,
    TrechoKMCliente,
    TrechoRateioKM,
)
from .services.identidade.grupo_mapping_service import (
    mapear_grupos_ad_para_django,
    validar_mapeamento_grupos_ad,
)
from .services.identidade.ldap_backend import ActiveDirectoryBackend
from .services.identidade.ldap_utils import (
    conta_ad_bloqueada,
    conta_ad_desativada,
    conta_ad_expirada,
    construir_snapshot_ldap,
    extrair_grupos_ad,
    normalizar_username_ad,
    usuario_ad_ativo,
)
from .services.autorizacao_service import (
    GRUPO_ADMIN_ERP,
    GRUPO_DOMAIN_ADMINS,
    GRUPO_FINANCEIRO,
    GRUPO_GESTOR,
    GRUPO_TECNICO,
    queryset_relatorios_visiveis,
    status_permite_edicao_relatorio_alheio_autorizado,
    status_permite_edicao_relatorio_proprio,
    status_permite_envio_relatorio,
    usuario_eh_administrativo,
    usuario_eh_admin_extra,
    usuario_pode_acessar_financeiro,
    usuario_pode_aprovar_relatorio,
    usuario_pode_devolver_relatorio_ajuste,
    usuario_pode_editar_relatorio,
    usuario_pode_enviar_relatorio,
    usuario_pode_acessar_manutencao,
    usuario_pode_atuar_como_financeiro,
    usuario_pode_gerenciar_clientes,
    usuario_pode_reabrir_relatorio,
    usuario_pode_rejeitar_relatorio,
    usuario_pode_visualizar_relatorio,
    usuario_tem_acesso_total,
)
from .services.permissoes_service import (
    CodigoPermissao,
    avaliar_permissao_cutover,
    definir_override_permissao,
    estado_efetivo_override,
    listar_permissoes,
    obter_permissao,
    usuario_tem_permissao,
    usuario_tem_permissao_central,
    usuario_tem_full_access_erp,
    usuario_pode_acessar_central_permissoes,
)
from .context_processors import permissoes_erp
from .services.resumo_cliente_service import resumo_financeiro_por_cliente
from .services.clientes_valor_km_service import (
    clientes_relatorio_sem_valor_km,
    usuario_pode_configurar_valor_km,
)
from .services.help_center_service import usuario_pode_editar_ajuda
from .services.clientes_relatorio_service import resolver_cliente_empresa_grupo
from .services.financeiro_validator import validar_integridade_financeira_relatorio
from .services.km_financeiro_service import calcular_km_financeiro
from .services.periodo_despesa_service import calcular_diarias_periodo
from .services.politica_aprovacao_service import aplicar_politica_valor_aprovado_inicial
from .services.politica_valor_service import (
    resolver_politica_despesa,
    validar_configuracao_politica_empresas,
    valor_km_control_sul,
)
from .services.relatorio_performance_service import RelatorioPerformanceTracker
from .services.usuarios_permissoes_service import preparar_preview_replicacao_permissoes
from .services.notificacoes_service import obter_notificacoes_usuario
from .services.workflow_service import aprovar_relatorio
from .services.snapshot_service import criar_snapshot_financeiro
from .services.tecnicos_despesa_service import (
    remover_tecnicos_despesas_fora_relatorio,
    sync_tecnicos_despesa,
)
from .services.identidade.sincronizacao_service import (
    UsuarioExternoSnapshot,
    sincronizar_usuario_externo,
)


class _GrupoFake:
    def __init__(self, nomes=()):
        self.nomes = set(nomes)
        self._filtro = set()

    def filter(self, **kwargs):
        valores = kwargs.get("name__in")
        if valores is None and "name" in kwargs:
            valores = [kwargs["name"]]
        self._filtro = set(valores or [])
        return self

    def exists(self):
        return bool(self.nomes.intersection(self._filtro))


class _UsuarioFake:
    is_authenticated = True
    is_superuser = False
    pk = 1

    def __init__(self, username, grupos=()):
        self.username = username
        self.groups = _GrupoFake(grupos)

    def get_username(self):
        return self.username


class RelatorioPerformanceTrackerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request_post(self):
        return self.factory.post(
            "/relatorios/novo/",
            data={
                "despesas-TOTAL_FORMS": "2",
                "trechos-TOTAL_FORMS": "1",
                "tecnico_responsavel": "10",
                "tecnicos_equipe": ["11", "12"],
                "despesas-0-comprovante": SimpleUploadedFile(
                    "comprovante.pdf",
                    b"abc",
                    content_type="application/pdf",
                ),
            },
        )

    @override_settings(RELATORIO_PERF_ENABLED=False)
    def test_relatorio_perf_desligado_nao_emite_log(self):
        request = self._request_post()
        with patch("relatorios.services.relatorio_performance_service.logger.info") as info:
            tracker = RelatorioPerformanceTracker(request)
            with tracker.phase("validation"):
                pass
            tracker.finish(outcome="success", http_status=302)

        info.assert_not_called()

    @override_settings(RELATORIO_PERF_ENABLED=True, RELATORIO_PERF_SLOW_SECONDS=999)
    def test_relatorio_perf_ligado_registra_resumo_e_server_timing(self):
        request = self._request_post()
        with patch("relatorios.services.relatorio_performance_service.logger.info") as info:
            tracker = RelatorioPerformanceTracker(request)
            with tracker.phase("validation"):
                pass
            tracker.finish(outcome="validation_error", http_status=422)
            response = tracker.apply_server_timing(HttpResponse())

        mensagem = info.call_args.args[0]
        self.assertIn("[RELATORIO_PERF]", mensagem)
        self.assertIn("outcome=validation_error", mensagem)
        self.assertIn("http_status=422", mensagem)
        self.assertIn("files=1", mensagem)
        self.assertIn("upload_bytes=3", mensagem)
        self.assertIn("expenses=2", mensagem)
        self.assertIn("km=1", mensagem)
        self.assertIn("participants=3", mensagem)
        self.assertIn("validation;dur=", response["Server-Timing"])


class ExtraAdminUsersTests(SimpleTestCase):
    @override_settings(EXTRA_ADMIN_USERS=["joao.martins"])
    def test_usuario_extra_admin_tem_acesso_administrativo(self):
        usuario = _UsuarioFake("JOAO.MARTINS")

        self.assertTrue(usuario_eh_admin_extra(usuario))
        self.assertTrue(usuario_tem_acesso_total(usuario))
        self.assertTrue(usuario_eh_administrativo(usuario))

    @override_settings(EXTRA_ADMIN_USERS=["joao.martins"])
    def test_usuario_fora_da_lista_nao_recebe_excecao(self):
        usuario = _UsuarioFake("usuario.comum")

        self.assertFalse(usuario_eh_admin_extra(usuario))
        self.assertFalse(usuario_tem_acesso_total(usuario))
        self.assertFalse(usuario_eh_administrativo(usuario))

    @override_settings(EXTRA_ADMIN_USERS=[])
    def test_admin_via_ad_continua_funcionando(self):
        usuario = _UsuarioFake("admin.ad", grupos=["Domain Admins"])

        self.assertFalse(usuario_eh_admin_extra(usuario))
        self.assertTrue(usuario_tem_acesso_total(usuario))
        self.assertTrue(usuario_eh_administrativo(usuario))

    def test_usuario_administrativo_pode_editar_e_enviar_rascunho(self):
        usuario = _UsuarioFake("jaciara.colvero", grupos=[GRUPO_ADMIN_ERP])
        relatorio = SimpleNamespace(
            pk=55,
            numero=None,
            status=StatusRelatorio.RASCUNHO,
            criado_por_id=999,
            criado_por=None,
            tecnico_responsavel=None,
            tecnico_reembolso=None,
        )

        self.assertFalse(usuario_tem_acesso_total(usuario))
        self.assertTrue(usuario_eh_administrativo(usuario))
        self.assertTrue(usuario_pode_editar_relatorio(usuario, relatorio))
        self.assertTrue(usuario_pode_enviar_relatorio(usuario, relatorio))

    def test_usuario_administrativo_nao_envia_status_nao_permitido(self):
        usuario = _UsuarioFake("jaciara.colvero", grupos=[GRUPO_ADMIN_ERP])
        relatorio = SimpleNamespace(
            pk=55,
            numero=None,
            status=StatusRelatorio.CONFERENCIA,
            criado_por_id=999,
            criado_por=None,
            tecnico_responsavel=None,
            tecnico_reembolso=None,
        )

        self.assertFalse(usuario_pode_enviar_relatorio(usuario, relatorio))

    def test_dono_nao_edita_relatorio_em_conferencia(self):
        usuario = _UsuarioFake("tecnico.dono")
        relatorio = SimpleNamespace(
            pk=56,
            numero=None,
            status=StatusRelatorio.CONFERENCIA,
            criado_por_id=usuario.pk,
            criado_por=None,
            tecnico_responsavel=None,
            tecnico_reembolso=None,
        )

        self.assertFalse(usuario_pode_editar_relatorio(usuario, relatorio))
        self.assertFalse(usuario_pode_enviar_relatorio(usuario, relatorio))

    def test_financeiro_edita_relatorio_em_conferencia(self):
        usuario = _UsuarioFake("financeiro", grupos=["Financeiro"])
        relatorio = SimpleNamespace(
            pk=57,
            numero=None,
            status=StatusRelatorio.CONFERENCIA,
            criado_por_id=999,
            criado_por=None,
            tecnico_responsavel=None,
            tecnico_reembolso=None,
        )

        self.assertTrue(usuario_pode_editar_relatorio(usuario, relatorio))
        self.assertFalse(usuario_pode_enviar_relatorio(usuario, relatorio))


class PermissoesServiceTests(SimpleTestCase):
    def _relatorio(self, status, criado_por_id=999, tecnico_email=""):
        return SimpleNamespace(
            pk=88,
            numero=None,
            status=status,
            criado_por_id=criado_por_id,
            criado_por=None,
            tecnico_responsavel=SimpleNamespace(
                pk=77,
                nome="Tecnico",
                email=tecnico_email,
            ) if tecnico_email else None,
            tecnico_reembolso=None,
        )

    def test_catalogo_nao_tem_codigos_duplicados(self):
        codigos = [permissao.codigo for permissao in listar_permissoes()]

        self.assertEqual(len(codigos), len(set(codigos)))
        self.assertIsNotNone(obter_permissao(CodigoPermissao.MANUTENCAO_ACESSAR))

    def test_codigo_inexistente_falha_fechado(self):
        usuario = _UsuarioFake("usuario.comum")

        self.assertFalse(usuario_tem_permissao(usuario, "codigo.inexistente"))

    def test_permissao_global_reproduz_regra_legada_permitida(self):
        usuario = _UsuarioFake("admin.erp", grupos=[GRUPO_ADMIN_ERP])

        self.assertEqual(
            usuario_pode_acessar_manutencao(usuario),
            usuario_tem_permissao(usuario, CodigoPermissao.MANUTENCAO_ACESSAR),
        )

    def test_permissao_global_reproduz_regra_legada_negada(self):
        usuario = _UsuarioFake("usuario.comum")

        self.assertEqual(
            usuario_pode_acessar_manutencao(usuario),
            usuario_tem_permissao(usuario, CodigoPermissao.MANUTENCAO_ACESSAR),
        )

    def test_superuser_preserva_acesso_herdado(self):
        usuario = _UsuarioFake("root")
        usuario.is_superuser = True

        self.assertTrue(usuario_tem_permissao(usuario, CodigoPermissao.ERP_ACESSAR))
        self.assertTrue(usuario_tem_permissao(usuario, CodigoPermissao.FINANCEIRO_ATUAR))
        self.assertTrue(usuario_tem_permissao(usuario, CodigoPermissao.MANUTENCAO_ACESSAR))

    def test_grupo_financeiro_preserva_regra_financeira(self):
        usuario = _UsuarioFake("financeiro", grupos=[GRUPO_FINANCEIRO])

        self.assertEqual(
            usuario_pode_atuar_como_financeiro(usuario),
            usuario_tem_permissao(usuario, CodigoPermissao.RELATORIOS_APROVAR),
        )
        self.assertEqual(
            usuario_pode_atuar_como_financeiro(usuario),
            usuario_tem_permissao(usuario, CodigoPermissao.FINANCEIRO_ALTERAR_RATEIOS),
        )

    def test_permissao_por_objeto_exige_objeto(self):
        usuario = _UsuarioFake("tecnico.dono")

        self.assertFalse(usuario_tem_permissao(usuario, CodigoPermissao.RELATORIOS_EDITAR))

    def test_permissao_por_objeto_reproduz_edicao_por_status(self):
        usuario = _UsuarioFake("tecnico.dono")
        relatorio = self._relatorio(StatusRelatorio.RASCUNHO, criado_por_id=usuario.pk)

        self.assertEqual(
            usuario_pode_editar_relatorio(usuario, relatorio),
            usuario_tem_permissao(usuario, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio),
        )

    def test_permissao_por_status_bloqueia_status_finalizado(self):
        usuario = _UsuarioFake("admin.erp", grupos=[GRUPO_ADMIN_ERP])
        relatorio = self._relatorio(StatusRelatorio.APROVADO)

        self.assertEqual(
            usuario_pode_editar_relatorio(usuario, relatorio),
            usuario_tem_permissao(usuario, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio),
        )

    def test_permissao_enviar_reproduz_regra_legada(self):
        usuario = _UsuarioFake("responsavel")
        usuario.email = "tecnico@empresa.test"
        relatorio = self._relatorio(
            StatusRelatorio.RASCUNHO,
            criado_por_id=999,
            tecnico_email="tecnico@empresa.test",
        )

        self.assertEqual(
            usuario_pode_enviar_relatorio(usuario, relatorio),
            usuario_tem_permissao(usuario, CodigoPermissao.RELATORIOS_ENVIAR, objeto=relatorio),
        )

    @override_settings(EXTRA_ADMIN_USERS=[])
    def test_reabrir_reproduz_regra_hardcoded_atual(self):
        usuario_autorizado = _UsuarioFake("control.local\\gabriel.oliveira")
        usuario_negado = _UsuarioFake("admin.erp", grupos=[GRUPO_ADMIN_ERP])

        self.assertEqual(
            usuario_pode_reabrir_relatorio(usuario_autorizado),
            usuario_tem_permissao(usuario_autorizado, CodigoPermissao.RELATORIOS_REABRIR),
        )
        self.assertEqual(
            usuario_pode_reabrir_relatorio(usuario_negado),
            usuario_tem_permissao(usuario_negado, CodigoPermissao.RELATORIOS_REABRIR),
        )


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, ERP_FULL_ACCESS_USERS=[], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverPrimeiroLoteFlagOffTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(username="admin.erp")
        self.comum = self.User.objects.create_user(username="usuario.comum")
        self.staff = self.User.objects.create_user(username="staff.user", is_staff=True)
        grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        self.admin.groups.add(grupo_admin)

    def test_flag_off_manutencao_preserva_regra_legada_e_ignora_override(self):
        definir_override_permissao(
            self.admin,
            CodigoPermissao.MANUTENCAO_ACESSAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )

        self.assertTrue(usuario_pode_acessar_manutencao(self.admin))
        self.assertFalse(usuario_pode_acessar_manutencao(self.comum))

    def test_flag_off_clientes_preserva_regra_legada_e_ignora_override(self):
        definir_override_permissao(
            self.admin,
            CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )

        self.assertTrue(usuario_pode_gerenciar_clientes(self.admin))
        self.assertFalse(usuario_pode_gerenciar_clientes(self.comum))

    def test_flag_off_valor_km_preserva_regra_legada_e_ignora_override(self):
        definir_override_permissao(
            self.admin,
            CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )

        self.assertTrue(usuario_pode_configurar_valor_km(self.admin))
        self.assertFalse(usuario_pode_configurar_valor_km(self.comum))

    def test_flag_off_ajuda_preserva_regra_legada_incluindo_staff(self):
        definir_override_permissao(
            self.staff,
            CodigoPermissao.AJUDA_EDITAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )

        self.assertTrue(usuario_pode_editar_ajuda(self.staff))
        self.assertFalse(usuario_pode_editar_ajuda(self.comum))

    def test_shadow_log_divergencia_sem_alterar_resultado_legado(self):
        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            resultado = avaliar_permissao_cutover(
                self.admin,
                CodigoPermissao.MANUTENCAO_ACESSAR,
                True,
            )

        self.assertTrue(resultado)
        self.assertTrue(any("[PERMISSOES_SHADOW]" in mensagem for mensagem in logs.output))
        self.assertTrue(any("legacy=1 central=0" in mensagem for mensagem in logs.output))

    def test_shadow_log_central_permitido_nao_altera_negativa_legada(self):
        definir_override_permissao(
            self.comum,
            CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.admin,
        )

        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            resultado = avaliar_permissao_cutover(
                self.comum,
                CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR,
                False,
            )

        self.assertFalse(resultado)
        self.assertTrue(any("legacy=0 central=1" in mensagem for mensagem in logs.output))

    def test_shadow_nao_loga_quando_resultados_iguais(self):
        with patch("relatorios.services.permissoes_service.logger.info") as info:
            resultado = avaliar_permissao_cutover(
                self.comum,
                CodigoPermissao.MANUTENCAO_ACESSAR,
                False,
            )

        self.assertFalse(resultado)
        info.assert_not_called()


@override_settings(PERMISSOES_CENTRAL_ENABLED=True, ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverPrimeiroLoteFlagOnTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.operador = self.User.objects.create_user(username="operador")
        self.comum = self.User.objects.create_user(username="usuario.comum")
        self.superuser = self.User.objects.create_superuser(
            username="superuser.negocio",
            email="super@example.com",
            password="x",
        )
        self.staff = self.User.objects.create_user(username="staff.user", is_staff=True)
        self.full = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        self.domain = self.User.objects.create_user(username="domain.admin")
        self.admin_erp = self.User.objects.create_user(username="admin.erp")
        grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        self.domain.groups.add(grupo_domain)
        self.operador.groups.add(grupo_tecnico)
        self.admin_erp.groups.add(grupo_admin)

    def _permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

    def _negar(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.full,
        )

    def test_flag_on_permitir_herdar_negar_e_full_protegido(self):
        self.assertFalse(usuario_pode_acessar_manutencao(self.comum))
        self._permitir(self.comum, CodigoPermissao.MANUTENCAO_ACESSAR)
        self.assertTrue(usuario_pode_acessar_manutencao(self.comum))
        self._negar(self.comum, CodigoPermissao.MANUTENCAO_ACESSAR)
        self.assertFalse(usuario_pode_acessar_manutencao(self.comum))
        self.assertTrue(usuario_pode_acessar_manutencao(self.full))

    def test_flag_on_superuser_staff_domain_admin_nao_concedem_negocio_central(self):
        self.assertFalse(usuario_pode_acessar_manutencao(self.superuser))
        self.assertFalse(usuario_pode_editar_ajuda(self.staff))
        self.assertFalse(usuario_pode_gerenciar_clientes(self.domain))

    def test_clientes_e_valor_km_sao_permissoes_separadas(self):
        self._permitir(self.comum, CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR)
        self.assertTrue(usuario_pode_gerenciar_clientes(self.comum))
        self.assertFalse(usuario_pode_configurar_valor_km(self.comum))

        self._negar(self.comum, CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR)
        self._permitir(self.comum, CodigoPermissao.CLIENTES_CONFIGURAR_VALOR_KM)
        self.assertFalse(usuario_pode_gerenciar_clientes(self.comum))
        self.assertTrue(usuario_pode_configurar_valor_km(self.comum))

    def test_backend_cliente_list_exige_permissao_central(self):
        cliente_url = reverse("relatorios:cliente_list")
        self.client.force_login(self.operador)
        resposta_negada = self.client.get(cliente_url)
        self.assertEqual(resposta_negada.status_code, 302)

        self._permitir(self.operador, CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR)
        resposta_permitida = self.client.get(cliente_url)
        self.assertEqual(resposta_permitida.status_code, 200)

    def test_sidebar_flag_on_usa_permissoes_do_lote(self):
        request = RequestFactory().get("/")
        request.user = self.comum
        contexto = permissoes_erp(request)["permissoes_erp"]
        self.assertFalse(contexto["visualiza_clientes"])
        self.assertFalse(contexto["manutencao"])

        self._permitir(self.comum, CodigoPermissao.CADASTROS_CLIENTES_GERENCIAR)
        self._permitir(self.comum, CodigoPermissao.MANUTENCAO_ACESSAR)
        contexto = permissoes_erp(request)["permissoes_erp"]
        self.assertTrue(contexto["visualiza_clientes"])
        self.assertTrue(contexto["manutencao"])

        request.user = self.admin_erp
        contexto = permissoes_erp(request)["permissoes_erp"]
        self.assertFalse(contexto["visualiza_clientes"])
        self.assertTrue(contexto["visualiza_tecnicos"])
        self.assertTrue(contexto["visualiza_adiantamentos"])


class _RelatorioVisibilidadeMixin:
    def criar_relatorio_visibilidade(
        self,
        numero,
        criado_por,
        tecnico=None,
        status=StatusRelatorio.RASCUNHO,
    ):
        tecnico = tecnico or self.tecnico
        return RelatorioTecnico.objects.create(
            numero=str(numero),
            cliente=self.cliente,
            tecnico_responsavel=tecnico,
            cidade_atendimento="Curitiba",
            uf_atendimento="PR",
            tipo_localidade="interior",
            data_inicio=date(2026, 5, 1),
            data_fim=date(2026, 5, 2),
            motivo="Atendimento tecnico",
            criado_por=criado_por,
            status=status,
        )


def _marcar_cadastro_usuario_completo(usuario, first_name="Usuario", last_name=None):
    usuario.first_name = first_name
    usuario.last_name = last_name or usuario.username.replace(".", " ").title()
    username_email = usuario.username.replace("\\", ".")
    usuario.email = usuario.email or f"{username_email}@example.com"
    usuario.save(update_fields=["first_name", "last_name", "email"])
    PerfilUsuario.objects.update_or_create(
        usuario=usuario,
        defaults={"cadastro_confirmado_em": timezone.now()},
    )


class RelatorioStatusAutorizacaoTests(SimpleTestCase):
    def test_status_de_edicao_do_proprio_relatorio(self):
        self.assertTrue(status_permite_edicao_relatorio_proprio(StatusRelatorio.RASCUNHO))
        self.assertTrue(status_permite_edicao_relatorio_proprio(StatusRelatorio.AJUSTE))
        self.assertFalse(status_permite_edicao_relatorio_proprio(StatusRelatorio.CONFERENCIA))
        self.assertFalse(status_permite_edicao_relatorio_proprio(StatusRelatorio.APROVADO))
        self.assertFalse(status_permite_edicao_relatorio_proprio(StatusRelatorio.REJEITADO))

    def test_status_de_edicao_alheia_autorizada_bloqueia_conferencia(self):
        self.assertTrue(status_permite_edicao_relatorio_alheio_autorizado(StatusRelatorio.RASCUNHO))
        self.assertTrue(status_permite_edicao_relatorio_alheio_autorizado(StatusRelatorio.AJUSTE))
        self.assertFalse(status_permite_edicao_relatorio_alheio_autorizado(StatusRelatorio.CONFERENCIA))
        self.assertFalse(status_permite_edicao_relatorio_alheio_autorizado(StatusRelatorio.APROVADO))
        self.assertFalse(status_permite_edicao_relatorio_alheio_autorizado(StatusRelatorio.REJEITADO))

    def test_status_de_envio_e_reenvio(self):
        self.assertTrue(status_permite_envio_relatorio(StatusRelatorio.RASCUNHO))
        self.assertTrue(status_permite_envio_relatorio(StatusRelatorio.AJUSTE))
        self.assertFalse(status_permite_envio_relatorio(StatusRelatorio.CONFERENCIA))
        self.assertFalse(status_permite_envio_relatorio(StatusRelatorio.APROVADO))
        self.assertFalse(status_permite_envio_relatorio(StatusRelatorio.REJEITADO))


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, ERP_FULL_ACCESS_USERS=[], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverRelatoriosVisibilidadeFlagOffTests(_RelatorioVisibilidadeMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")
        self.admin = self.User.objects.create_user(username="admin.erp")
        self.responsavel = self.User.objects.create_user(
            username="responsavel",
            email="responsavel@example.com",
        )
        grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.admin.groups.add(grupo_admin)
        self.dono.groups.add(grupo_tecnico)
        self.outro.groups.add(grupo_tecnico)
        self.responsavel.groups.add(grupo_tecnico)
        self.cliente = Cliente.objects.create(nome="Cliente Visibilidade", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Padrao", email="tecnico@example.com")
        self.tecnico_responsavel = Tecnico.objects.create(
            nome="Tecnico Responsavel",
            email="responsavel@example.com",
        )
        self.relatorio_dono = self.criar_relatorio_visibilidade(99001, self.dono)
        self.relatorio_outro = self.criar_relatorio_visibilidade(99002, self.outro)
        self.relatorio_responsavel = self.criar_relatorio_visibilidade(
            99003,
            self.outro,
            tecnico=self.tecnico_responsavel,
        )

    def test_flag_off_preserva_dono_admin_e_comportamento_legado_do_responsavel(self):
        self.assertTrue(usuario_pode_visualizar_relatorio(self.dono, self.relatorio_dono))
        self.assertTrue(usuario_pode_visualizar_relatorio(self.admin, self.relatorio_outro))
        self.assertEqual(
            usuario_pode_visualizar_relatorio(self.responsavel, self.relatorio_responsavel),
            False,
        )

    def test_flag_off_queryset_preserva_legado_e_override_nao_muda_produtivo(self):
        definir_override_permissao(
            self.dono,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.admin,
        )

        ids = set(
            queryset_relatorios_visiveis(
                self.dono,
                RelatorioTecnico.objects.all(),
            ).values_list("pk", flat=True)
        )

        self.assertIn(self.relatorio_dono.pk, ids)
        self.assertNotIn(self.relatorio_outro.pk, ids)

    def test_flag_off_url_direta_mantem_comportamento_legado(self):
        self.client.force_login(self.dono)
        resposta = self.client.get(reverse("relatorios:relatorio_detail", args=[self.relatorio_outro.pk]))

        self.assertEqual(resposta.status_code, 404)

    def test_flag_off_shadow_loga_divergencia_de_visibilidade_sem_mudar_resultado(self):
        definir_override_permissao(
            self.dono,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.admin,
        )

        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            resultado = usuario_pode_visualizar_relatorio(self.dono, self.relatorio_outro)

        self.assertFalse(resultado)
        self.assertTrue(any("[PERMISSOES_SHADOW]" in mensagem for mensagem in logs.output))
        self.assertTrue(any("object=RelatorioTecnico" in mensagem for mensagem in logs.output))


@override_settings(PERMISSOES_CENTRAL_ENABLED=True, ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverRelatoriosVisibilidadeFlagOnTests(_RelatorioVisibilidadeMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")
        self.universal = self.User.objects.create_user(username="universal")
        self.superuser = self.User.objects.create_superuser(
            username="superuser.sem.full",
            email="superuser@example.com",
            password="x",
        )
        self.domain = self.User.objects.create_user(username="domain.admin")
        self.full = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        self.responsavel = self.User.objects.create_user(
            username="responsavel",
            email="responsavel@example.com",
        )
        grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.domain.groups.add(grupo_domain)
        self.dono.groups.add(grupo_tecnico)
        self.outro.groups.add(grupo_tecnico)
        self.universal.groups.add(grupo_tecnico)
        self.responsavel.groups.add(grupo_tecnico)
        self.cliente = Cliente.objects.create(nome="Cliente Visibilidade Central", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Padrao Central", email="tecnico.central@example.com")
        self.tecnico_responsavel = Tecnico.objects.create(
            nome="Tecnico Responsavel Central",
            email="responsavel@example.com",
        )
        self.relatorio_dono = self.criar_relatorio_visibilidade(99101, self.dono)
        self.relatorio_outro = self.criar_relatorio_visibilidade(99102, self.outro)
        self.relatorio_responsavel = self.criar_relatorio_visibilidade(
            99103,
            self.outro,
            tecnico=self.tecnico_responsavel,
        )

    def _permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

    def _negar(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.full,
        )

    def test_flag_on_dono_visualiza_proprio_e_sem_universal_nao_visualiza_alheio(self):
        self.assertTrue(usuario_pode_visualizar_relatorio(self.dono, self.relatorio_dono))
        self.assertFalse(usuario_pode_visualizar_relatorio(self.dono, self.relatorio_outro))

    def test_flag_on_permitir_visualizar_alheios_concede_e_negar_bloqueia(self):
        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.assertTrue(usuario_pode_visualizar_relatorio(self.universal, self.relatorio_outro))

        self._negar(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.assertFalse(usuario_pode_visualizar_relatorio(self.universal, self.relatorio_outro))

    def test_flag_on_tecnico_responsavel_sozinho_nao_visualiza_alheio(self):
        self.assertFalse(usuario_pode_visualizar_relatorio(self.responsavel, self.relatorio_responsavel))

    def test_flag_on_full_protegido_visualiza_alheio(self):
        self.assertTrue(usuario_pode_visualizar_relatorio(self.full, self.relatorio_outro))

    def test_flag_on_superuser_e_domain_admin_sozinhos_nao_visualizam_alheio(self):
        self.assertFalse(usuario_pode_visualizar_relatorio(self.superuser, self.relatorio_outro))
        self.assertFalse(usuario_pode_visualizar_relatorio(self.domain, self.relatorio_outro))

    def test_flag_on_queryset_sem_universal_contem_somente_criado_por(self):
        ids = set(
            queryset_relatorios_visiveis(
                self.dono,
                RelatorioTecnico.objects.all(),
            ).values_list("pk", flat=True)
        )

        self.assertIn(self.relatorio_dono.pk, ids)
        self.assertNotIn(self.relatorio_outro.pk, ids)
        self.assertNotIn(self.relatorio_responsavel.pk, ids)

    def test_flag_on_queryset_com_universal_inclui_alheios(self):
        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        ids = set(
            queryset_relatorios_visiveis(
                self.universal,
                RelatorioTecnico.objects.all(),
            ).values_list("pk", flat=True)
        )

        self.assertIn(self.relatorio_dono.pk, ids)
        self.assertIn(self.relatorio_outro.pk, ids)

    def test_flag_on_listagem_e_url_direta_sao_consistentes(self):
        self.client.force_login(self.dono)
        resposta_negada = self.client.get(reverse("relatorios:relatorio_detail", args=[self.relatorio_outro.pk]))
        self.assertEqual(resposta_negada.status_code, 404)

        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        resposta_permitida = self.client.get(reverse("relatorios:relatorio_detail", args=[self.relatorio_outro.pk]))
        self.assertEqual(resposta_permitida.status_code, 200)


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, ERP_FULL_ACCESS_USERS=[], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverRelatoriosEdicaoEnvioFlagOffTests(_RelatorioVisibilidadeMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")
        self.admin = self.User.objects.create_user(username="admin.erp")
        self.responsavel = self.User.objects.create_user(
            username="responsavel",
            email="responsavel@example.com",
        )
        for usuario in [self.dono, self.outro, self.admin, self.responsavel]:
            _marcar_cadastro_usuario_completo(usuario)
        grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.admin.groups.add(grupo_admin)
        self.dono.groups.add(grupo_tecnico)
        self.outro.groups.add(grupo_tecnico)
        self.responsavel.groups.add(grupo_tecnico)
        self.cliente = Cliente.objects.create(nome="Cliente Edicao Legado", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Padrao Edicao", email="tecnico@example.com")
        self.tecnico_responsavel = Tecnico.objects.create(
            nome="Tecnico Responsavel Edicao",
            email="responsavel@example.com",
        )
        self.relatorio_dono = self.criar_relatorio_visibilidade(99201, self.dono)
        self.relatorio_dono_aprovado = self.criar_relatorio_visibilidade(
            99202,
            self.dono,
            status=StatusRelatorio.APROVADO,
        )
        self.relatorio_dono_conferencia = self.criar_relatorio_visibilidade(
            99203,
            self.dono,
            status=StatusRelatorio.CONFERENCIA,
        )
        self.relatorio_responsavel = self.criar_relatorio_visibilidade(
            99204,
            self.outro,
            tecnico=self.tecnico_responsavel,
        )

    def test_flag_off_dono_preserva_edicao_e_envio_por_status_legado(self):
        self.assertTrue(usuario_pode_editar_relatorio(self.dono, self.relatorio_dono))
        self.assertFalse(usuario_pode_editar_relatorio(self.dono, self.relatorio_dono_aprovado))
        self.assertTrue(usuario_pode_enviar_relatorio(self.dono, self.relatorio_dono))
        self.assertFalse(usuario_pode_enviar_relatorio(self.dono, self.relatorio_dono_conferencia))

    def test_flag_off_tecnico_responsavel_alheio_mantem_comportamento_legado(self):
        self.assertTrue(usuario_pode_editar_relatorio(self.responsavel, self.relatorio_responsavel))
        self.assertTrue(usuario_pode_enviar_relatorio(self.responsavel, self.relatorio_responsavel))

    def test_flag_off_administrativo_mantem_comportamento_legado(self):
        self.assertTrue(usuario_pode_editar_relatorio(self.admin, self.relatorio_responsavel))
        self.assertTrue(usuario_pode_enviar_relatorio(self.admin, self.relatorio_responsavel))

    def test_flag_off_override_central_divergente_nao_muda_resultado_e_loga_shadow(self):
        definir_override_permissao(
            self.responsavel,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )
        definir_override_permissao(
            self.responsavel,
            CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.admin,
        )

        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            pode_editar = usuario_pode_editar_relatorio(self.responsavel, self.relatorio_responsavel)
            pode_enviar = usuario_pode_enviar_relatorio(self.responsavel, self.relatorio_responsavel)

        self.assertTrue(pode_editar)
        self.assertTrue(pode_enviar)
        mensagens = "\n".join(logs.output)
        self.assertIn("[PERMISSOES_SHADOW]", mensagens)
        self.assertIn(CodigoPermissao.RELATORIOS_EDITAR, mensagens)
        self.assertIn(CodigoPermissao.RELATORIOS_ENVIAR, mensagens)

    def test_flag_off_url_direta_de_edicao_preserva_legado(self):
        self.client.force_login(self.admin)
        resposta = self.client.get(reverse("relatorios:relatorio_update", args=[self.relatorio_responsavel.pk]))

        self.assertEqual(resposta.status_code, 200)

    def test_flag_off_botao_editar_na_listagem_preserva_legado(self):
        self.client.force_login(self.admin)
        resposta = self.client.get(reverse("relatorios:relatorio_list"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(
            resposta,
            reverse("relatorios:relatorio_update", args=[self.relatorio_responsavel.pk]),
        )


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=True,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class PermissoesCutoverRelatoriosEdicaoEnvioFlagOnTests(_RelatorioVisibilidadeMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")
        self.universal = self.User.objects.create_user(username="universal")
        self.editor = self.User.objects.create_user(username="editor")
        self.superuser = self.User.objects.create_superuser(
            username="superuser.sem.full",
            email="superuser@example.com",
            password="x",
        )
        self.domain = self.User.objects.create_user(username="domain.admin")
        self.full = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        self.responsavel = self.User.objects.create_user(
            username="responsavel",
            email="responsavel@example.com",
        )
        for usuario in [
            self.dono,
            self.outro,
            self.universal,
            self.editor,
            self.superuser,
            self.domain,
            self.full,
            self.responsavel,
        ]:
            _marcar_cadastro_usuario_completo(usuario)
        grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.domain.groups.add(grupo_domain)
        for usuario in [self.dono, self.outro, self.universal, self.editor, self.responsavel]:
            usuario.groups.add(grupo_tecnico)
        self.cliente = Cliente.objects.create(nome="Cliente Edicao Central", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Padrao Central", email="tecnico.central@example.com")
        self.tecnico_responsavel = Tecnico.objects.create(
            nome="Tecnico Responsavel Central",
            email="responsavel@example.com",
        )
        self.relatorio_dono = self.criar_relatorio_visibilidade(99301, self.dono)
        self.relatorio_dono_aprovado = self.criar_relatorio_visibilidade(
            99302,
            self.dono,
            status=StatusRelatorio.APROVADO,
        )
        self.relatorio_dono_conferencia = self.criar_relatorio_visibilidade(
            99303,
            self.dono,
            status=StatusRelatorio.CONFERENCIA,
        )
        self.relatorio_outro = self.criar_relatorio_visibilidade(99304, self.outro)
        self.relatorio_outro_aprovado = self.criar_relatorio_visibilidade(
            99305,
            self.outro,
            status=StatusRelatorio.APROVADO,
        )
        self.relatorio_responsavel = self.criar_relatorio_visibilidade(
            99306,
            self.outro,
            tecnico=self.tecnico_responsavel,
        )

    def _permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

    def _permitir_edicao_alheia(self, usuario):
        self._permitir(usuario, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(usuario, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS)

    def test_flag_on_edicao_propria_respeita_workflow_sem_toggle(self):
        self.assertTrue(usuario_pode_editar_relatorio(self.dono, self.relatorio_dono))
        self.assertFalse(usuario_pode_editar_relatorio(self.dono, self.relatorio_dono_aprovado))

    def test_flag_on_edicao_alheia_exige_visualizar_e_editar_alheios(self):
        self.assertFalse(usuario_pode_editar_relatorio(self.editor, self.relatorio_outro))

        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.assertFalse(usuario_pode_editar_relatorio(self.universal, self.relatorio_outro))

        self._permitir_edicao_alheia(self.editor)
        self.assertTrue(usuario_pode_editar_relatorio(self.editor, self.relatorio_outro))
        self.assertFalse(usuario_pode_editar_relatorio(self.editor, self.relatorio_outro_aprovado))

    def test_flag_on_tecnico_responsavel_superuser_e_domain_nao_bypassam_edicao(self):
        self.assertFalse(usuario_pode_editar_relatorio(self.responsavel, self.relatorio_responsavel))
        self.assertFalse(usuario_pode_editar_relatorio(self.superuser, self.relatorio_outro))
        self.assertFalse(usuario_pode_editar_relatorio(self.domain, self.relatorio_outro))

    def test_flag_on_full_protegido_edita_respeitando_workflow(self):
        self.assertTrue(usuario_pode_editar_relatorio(self.full, self.relatorio_outro))
        self.assertFalse(usuario_pode_editar_relatorio(self.full, self.relatorio_outro_aprovado))

    def test_flag_on_envio_proprio_respeita_workflow_sem_toggle(self):
        self.assertTrue(usuario_pode_enviar_relatorio(self.dono, self.relatorio_dono))
        self.assertFalse(usuario_pode_enviar_relatorio(self.dono, self.relatorio_dono_conferencia))

    def test_flag_on_envio_alheio_e_derivado_de_visualizar_e_editar_alheios(self):
        self.assertFalse(usuario_pode_enviar_relatorio(self.editor, self.relatorio_outro))

        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.assertFalse(usuario_pode_enviar_relatorio(self.universal, self.relatorio_outro))

        self._permitir_edicao_alheia(self.editor)
        self.assertTrue(usuario_pode_enviar_relatorio(self.editor, self.relatorio_outro))

    def test_flag_on_tecnico_responsavel_sozinho_nao_envia_alheio(self):
        self.assertFalse(usuario_pode_enviar_relatorio(self.responsavel, self.relatorio_responsavel))

    def test_flag_on_full_protegido_envia_respeitando_workflow(self):
        self.assertTrue(usuario_pode_enviar_relatorio(self.full, self.relatorio_outro))
        self.assertFalse(usuario_pode_enviar_relatorio(self.full, self.relatorio_dono_conferencia))

    def test_flag_on_relatorios_enviar_nao_exige_override_proprio(self):
        self._permitir_edicao_alheia(self.editor)

        self.assertFalse(
            PermissaoUsuarioOverride.objects.filter(
                usuario=self.editor,
                codigo=CodigoPermissao.RELATORIOS_ENVIAR,
            ).exists()
        )
        self.assertTrue(usuario_pode_enviar_relatorio(self.editor, self.relatorio_outro))

    def test_flag_on_post_direto_envio_alheio_sem_editar_alheios_e_bloqueado(self):
        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.client.force_login(self.universal)

        resposta = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                args=[self.relatorio_outro.pk, StatusRelatorio.CONFERENCIA],
            )
        )

        self.assertEqual(resposta.status_code, 302)
        self.relatorio_outro.refresh_from_db()
        self.assertEqual(self.relatorio_outro.status, StatusRelatorio.RASCUNHO)

    def test_flag_on_botao_editar_na_listagem_acompanha_backend(self):
        url_edicao = reverse("relatorios:relatorio_update", args=[self.relatorio_outro.pk])

        self._permitir(self.universal, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.client.force_login(self.universal)
        resposta_universal = self.client.get(reverse("relatorios:relatorio_list"))

        self.assertEqual(resposta_universal.status_code, 200)
        self.assertNotContains(resposta_universal, url_edicao)

        self._permitir_edicao_alheia(self.editor)
        self.client.force_login(self.editor)
        resposta_editor = self.client.get(reverse("relatorios:relatorio_list"))

        self.assertEqual(resposta_editor.status_code, 200)
        self.assertContains(resposta_editor, url_edicao)


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, ERP_FULL_ACCESS_USERS=[], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverFinanceiroAcessoFlagOffTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.financeiro = self.User.objects.create_user(username="financeiro.legado")
        self.operador = self.User.objects.create_user(username="operador")
        _marcar_cadastro_usuario_completo(self.financeiro)
        _marcar_cadastro_usuario_completo(self.operador)
        grupo_financeiro = Group.objects.create(name=GRUPO_FINANCEIRO)
        self.financeiro.groups.add(grupo_financeiro)

    def test_flag_off_preserva_acesso_operacional_legado(self):
        self.assertTrue(usuario_pode_atuar_como_financeiro(self.financeiro))
        self.assertTrue(usuario_pode_acessar_financeiro(self.financeiro))
        self.assertFalse(usuario_pode_acessar_financeiro(self.operador))

    def test_flag_off_override_divergente_nao_muda_decisao_e_loga_shadow(self):
        definir_override_permissao(
            self.financeiro,
            CodigoPermissao.FINANCEIRO_ACESSAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.operador,
        )

        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            resultado = usuario_pode_acessar_financeiro(self.financeiro)

        self.assertTrue(resultado)
        mensagens = "\n".join(logs.output)
        self.assertIn("[PERMISSOES_SHADOW]", mensagens)
        self.assertIn(CodigoPermissao.FINANCEIRO_ACESSAR, mensagens)


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=True,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class PermissoesCutoverFinanceiroAcessoFlagOnTests(_RelatorioVisibilidadeMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.operador = self.User.objects.create_user(username="operador")
        self.financeiro_grupo = self.User.objects.create_user(username="financeiro.grupo")
        self.gestor = self.User.objects.create_user(username="gestor")
        self.admin_erp = self.User.objects.create_user(username="admin.erp")
        self.domain = self.User.objects.create_user(username="domain.admin")
        self.superuser = self.User.objects.create_superuser(
            username="superuser.sem.full",
            email="superuser@example.com",
            password="x",
        )
        self.staff = self.User.objects.create_user(username="staff")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.full = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")
        for usuario in [
            self.operador,
            self.financeiro_grupo,
            self.gestor,
            self.admin_erp,
            self.domain,
            self.superuser,
            self.staff,
            self.full,
            self.dono,
            self.outro,
        ]:
            _marcar_cadastro_usuario_completo(usuario)

        grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        grupo_financeiro = Group.objects.create(name=GRUPO_FINANCEIRO)
        grupo_gestor = Group.objects.create(name=GRUPO_GESTOR)
        grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)

        self.financeiro_grupo.groups.add(grupo_financeiro)
        self.gestor.groups.add(grupo_gestor)
        self.admin_erp.groups.add(grupo_admin)
        self.domain.groups.add(grupo_domain)
        for usuario in [self.operador, self.dono, self.outro]:
            usuario.groups.add(grupo_tecnico)

        self.cliente = Cliente.objects.create(
            nome="Cliente Financeiro 4A",
            cidade="Curitiba",
            uf="PR",
        )
        self.tecnico = Tecnico.objects.create(
            nome="Tecnico Financeiro 4A",
            email="tecnico.financeiro.4a@example.com",
        )
        self.relatorio_conferencia = self.criar_relatorio_visibilidade(
            99401,
            self.outro,
            status=StatusRelatorio.CONFERENCIA,
        )
        ItemDespesa.objects.create(
            relatorio=self.relatorio_conferencia,
            data=date(2026, 5, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco",
            valor=Decimal("45.00"),
        )

    def _permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

    def _negar(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.full,
        )

    def test_flag_on_financeiro_acessar_permitir_negar_default_e_full(self):
        self.assertFalse(usuario_pode_acessar_financeiro(self.operador))

        self._permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self.assertTrue(usuario_pode_acessar_financeiro(self.operador))

        self._negar(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self.assertFalse(usuario_pode_acessar_financeiro(self.operador))

        self.assertTrue(usuario_pode_acessar_financeiro(self.full))

    def test_flag_on_grupos_e_flags_legados_nao_concedem_acesso_financeiro_central(self):
        self.assertFalse(usuario_pode_acessar_financeiro(self.financeiro_grupo))
        self.assertFalse(usuario_pode_acessar_financeiro(self.gestor))
        self.assertFalse(usuario_pode_acessar_financeiro(self.admin_erp))
        self.assertFalse(usuario_pode_acessar_financeiro(self.domain))
        self.assertFalse(usuario_pode_acessar_financeiro(self.superuser))
        self.assertFalse(usuario_pode_acessar_financeiro(self.staff))

    def test_flag_on_dono_ou_tecnico_nao_ganha_acesso_financeiro(self):
        self.assertFalse(usuario_pode_acessar_financeiro(self.dono))
        self.assertFalse(usuario_pode_acessar_financeiro(self.outro))

    def test_financeiro_acessar_nao_concede_visibilidade_de_relatorio_alheio(self):
        self._permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)

        self.client.force_login(self.operador)
        resposta = self.client.get(
            reverse("relatorios:relatorio_detail", args=[self.relatorio_conferencia.pk])
        )

        self.assertEqual(resposta.status_code, 404)

    def test_avisos_financeiros_usam_acesso_operacional_sem_liberar_escrita(self):
        self._permitir(self.operador, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)

        self.client.force_login(self.operador)
        resposta = self.client.get(
            reverse("relatorios:relatorio_detail", args=[self.relatorio_conferencia.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "card-atencao-financeiro")
        self.assertContains(resposta, "Resumo Financeiro")
        self.assertNotContains(resposta, 'data-tour="detail-aprovar-relatorio"')
        self.assertNotContains(resposta, "Corrigir valor de KM")

    def test_notificacao_conferencia_usa_financeiro_acessar_e_nao_grupo_legado(self):
        self._permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)

        notificacoes_operador = obter_notificacoes_usuario(self.operador)
        notificacoes_grupo = obter_notificacoes_usuario(self.financeiro_grupo)

        self.assertTrue(
            any(item["tipo"] == "relatorios_conferencia" for item in notificacoes_operador)
        )
        self.assertFalse(
            any(item["tipo"] == "relatorios_conferencia" for item in notificacoes_grupo)
        )

    def test_post_financeiro_e_pdf_interno_permanecem_com_autorizacao_antiga(self):
        self._permitir(self.operador, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self._permitir(self.operador, CodigoPermissao.RELATORIOS_APROVAR)
        self._permitir(self.operador, CodigoPermissao.RELATORIOS_PDF_INTERNO)
        self.client.force_login(self.operador)

        resposta_status = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                args=[self.relatorio_conferencia.pk, StatusRelatorio.APROVADO],
            )
        )
        self.relatorio_conferencia.refresh_from_db()

        self.assertEqual(resposta_status.status_code, 302)
        self.assertEqual(self.relatorio_conferencia.status, StatusRelatorio.CONFERENCIA)

        self.relatorio_conferencia.status = StatusRelatorio.APROVADO
        self.relatorio_conferencia.save(update_fields=["status"])
        resposta_pdf = self.client.get(
            reverse("relatorios:relatorio_pdf_interno", args=[self.relatorio_conferencia.pk])
        )

        self.assertEqual(resposta_pdf.status_code, 302)


class _PermissoesWorkflowFinanceiroMixin(_RelatorioVisibilidadeMixin):
    def criar_usuario_completo(self, username, grupos=None, **kwargs):
        usuario = self.User.objects.create_user(username=username, **kwargs)
        _marcar_cadastro_usuario_completo(usuario)
        for grupo in grupos or []:
            usuario.groups.add(grupo)
        return usuario

    def criar_relatorio_workflow(self, criado_por, status=StatusRelatorio.CONFERENCIA):
        relatorio = self.criar_relatorio_visibilidade(
            99501 + RelatorioTecnico.objects.count(),
            criado_por,
            status=status,
        )
        relatorio.tecnico_reembolso = self.tecnico
        relatorio.save(update_fields=["tecnico_reembolso"])
        RelatorioCliente.objects.get_or_create(
            relatorio=relatorio,
            cliente=self.cliente,
            defaults={"motivo_viagem": "Atendimento tecnico"},
        )
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            data=date(2026, 5, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco workflow",
            valor=Decimal("50.00"),
            valor_aprovado=Decimal("50.00"),
            quem_pagou=QuemPagou.EMPRESA,
        )
        DespesaCliente.objects.create(despesa=despesa, cliente=self.cliente)
        DespesaRateio.objects.create(
            despesa=despesa,
            cliente=self.cliente,
            valor_original=Decimal("50.00"),
            valor_final=Decimal("50.00"),
            percentual=Decimal("100.0000"),
        )
        return relatorio, despesa

    def permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

    def negar(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.full,
        )

    def preparar_usuario_acao_alheia(self, usuario, *codigos):
        self.permitir(usuario, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.permitir(usuario, CodigoPermissao.FINANCEIRO_ACESSAR)
        for codigo in codigos:
            self.permitir(usuario, codigo)


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=True,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class PermissoesCutoverEdicaoConferenciaTests(_PermissoesWorkflowFinanceiroMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.grupo_financeiro = Group.objects.create(name=GRUPO_FINANCEIRO)
        self.grupo_gestor = Group.objects.create(name=GRUPO_GESTOR)
        self.grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        self.grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        self.full = self.criar_usuario_completo("CONTROL\\gabriel.oliveira", [self.grupo_tecnico])
        self.dono = self.criar_usuario_completo("dono", [self.grupo_tecnico])
        self.editor = self.criar_usuario_completo("editor", [self.grupo_tecnico])
        self.financeiro = self.criar_usuario_completo("financeiro", [self.grupo_financeiro])
        self.gestor = self.criar_usuario_completo("gestor", [self.grupo_gestor])
        self.domain = self.criar_usuario_completo("domain", [self.grupo_domain])
        self.superuser = self.User.objects.create_superuser(
            username="superuser.sem.full",
            email="superuser@example.com",
            password="x",
        )
        _marcar_cadastro_usuario_completo(self.superuser)
        self.cliente = Cliente.objects.create(nome="Cliente Workflow", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Workflow", email="tecnico.workflow@example.com")
        self.relatorio, self.despesa = self.criar_relatorio_workflow(self.dono)

    def test_conferencia_pendente_bloqueia_edicao_geral_para_todos(self):
        self.preparar_usuario_acao_alheia(
            self.editor,
            CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
        )
        usuarios = [
            self.dono,
            self.financeiro,
            self.gestor,
            self.domain,
            self.superuser,
            self.full,
            self.editor,
        ]

        for usuario in usuarios:
            with self.subTest(usuario=usuario.username):
                self.assertFalse(usuario_pode_editar_relatorio(usuario, self.relatorio))

    def test_botao_editar_nao_aparece_e_endpoint_edicao_bloqueia_conferencia(self):
        self.client.force_login(self.full)

        detalhe = self.client.get(reverse("relatorios:relatorio_detail", args=[self.relatorio.pk]))
        edicao = self.client.get(reverse("relatorios:relatorio_update", args=[self.relatorio.pk]))

        self.assertEqual(detalhe.status_code, 200)
        self.assertNotContains(detalhe, reverse("relatorios:relatorio_update", args=[self.relatorio.pk]))
        self.assertEqual(edicao.status_code, 302)
        self.assertIn(reverse("relatorios:relatorio_detail", args=[self.relatorio.pk]), edicao["Location"])

    def test_rascunho_e_ajuste_preservam_edicao_do_dono(self):
        relatorio_rascunho, _ = self.criar_relatorio_workflow(
            self.dono,
            status=StatusRelatorio.RASCUNHO,
        )
        relatorio_ajuste, _ = self.criar_relatorio_workflow(
            self.dono,
            status=StatusRelatorio.AJUSTE,
        )

        self.assertTrue(usuario_pode_editar_relatorio(self.dono, relatorio_rascunho))
        self.assertTrue(usuario_pode_editar_relatorio(self.dono, relatorio_ajuste))


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, ERP_FULL_ACCESS_USERS=[], EXTRA_ADMIN_USERS=[])
class PermissoesCutoverFinanceiroWorkflowFlagOffTests(_PermissoesWorkflowFinanceiroMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.grupo_financeiro = Group.objects.create(name=GRUPO_FINANCEIRO)
        self.full = self.criar_usuario_completo("full.local")
        self.financeiro = self.criar_usuario_completo("financeiro.legado", [self.grupo_financeiro])
        self.operador = self.criar_usuario_completo("operador")
        self.cliente = Cliente.objects.create(nome="Cliente Workflow Flag Off", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Workflow Flag Off", email="tecnico.workflow.off@example.com")
        self.relatorio, self.despesa = self.criar_relatorio_workflow(self.operador)

    def test_flag_off_preserva_decisao_legada_das_tres_acoes(self):
        self.assertTrue(usuario_pode_aprovar_relatorio(self.financeiro, self.relatorio))
        self.assertTrue(usuario_pode_rejeitar_relatorio(self.financeiro, self.relatorio))
        self.assertTrue(usuario_pode_devolver_relatorio_ajuste(self.financeiro, self.relatorio))
        self.assertFalse(usuario_pode_aprovar_relatorio(self.operador, self.relatorio))

    def test_flag_off_divergencia_central_gera_shadow_sem_mudar_decisao(self):
        self.negar(self.financeiro, CodigoPermissao.RELATORIOS_APROVAR)

        with self.assertLogs("relatorios.services.permissoes_service", level="INFO") as logs:
            resultado = usuario_pode_aprovar_relatorio(self.financeiro, self.relatorio)

        self.assertTrue(resultado)
        mensagens = "\n".join(logs.output)
        self.assertIn("[PERMISSOES_SHADOW]", mensagens)
        self.assertIn(CodigoPermissao.RELATORIOS_APROVAR, mensagens)


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=True,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class PermissoesCutoverFinanceiroWorkflowFlagOnTests(_PermissoesWorkflowFinanceiroMixin, TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.grupo_tecnico = Group.objects.create(name=GRUPO_TECNICO)
        self.grupo_financeiro = Group.objects.create(name=GRUPO_FINANCEIRO)
        self.grupo_gestor = Group.objects.create(name=GRUPO_GESTOR)
        self.grupo_admin = Group.objects.create(name=GRUPO_ADMIN_ERP)
        self.grupo_domain = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        self.full = self.criar_usuario_completo("CONTROL\\gabriel.oliveira")
        self.dono = self.criar_usuario_completo("dono.workflow", [self.grupo_tecnico])
        self.operador = self.criar_usuario_completo("operador.workflow", [self.grupo_tecnico])
        self.financeiro = self.criar_usuario_completo("financeiro.grupo", [self.grupo_financeiro])
        self.gestor = self.criar_usuario_completo("gestor.grupo", [self.grupo_gestor])
        self.admin_erp = self.criar_usuario_completo("admin.erp", [self.grupo_admin])
        self.domain = self.criar_usuario_completo("domain.admin", [self.grupo_domain])
        self.staff = self.criar_usuario_completo("staff")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.superuser = self.User.objects.create_superuser(
            username="superuser.sem.full",
            email="superuser@example.com",
            password="x",
        )
        _marcar_cadastro_usuario_completo(self.superuser)
        self.cliente = Cliente.objects.create(nome="Cliente Workflow Flag On", cidade="Curitiba", uf="PR")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Workflow Flag On", email="tecnico.workflow.on@example.com")
        self.relatorio, self.despesa = self.criar_relatorio_workflow(self.dono)

    def test_grupos_e_flags_legados_nao_concedem_acoes_centrais(self):
        for usuario in [
            self.financeiro,
            self.gestor,
            self.admin_erp,
            self.domain,
            self.staff,
            self.superuser,
        ]:
            with self.subTest(usuario=usuario.username):
                self.assertFalse(usuario_pode_aprovar_relatorio(usuario, self.relatorio))
                self.assertFalse(usuario_pode_rejeitar_relatorio(usuario, self.relatorio))
                self.assertFalse(usuario_pode_devolver_relatorio_ajuste(usuario, self.relatorio))

    def test_full_tem_capacidade_mas_respeita_workflow(self):
        self.assertTrue(usuario_pode_aprovar_relatorio(self.full, self.relatorio))
        relatorio_rascunho, _ = self.criar_relatorio_workflow(
            self.dono,
            status=StatusRelatorio.RASCUNHO,
        )

        self.assertFalse(usuario_pode_aprovar_relatorio(self.full, relatorio_rascunho))

    def test_permissoes_de_workflow_sao_independentes(self):
        self.preparar_usuario_acao_alheia(self.operador, CodigoPermissao.RELATORIOS_APROVAR)
        self.assertTrue(usuario_pode_aprovar_relatorio(self.operador, self.relatorio))
        self.assertFalse(usuario_pode_rejeitar_relatorio(self.operador, self.relatorio))
        self.assertFalse(usuario_pode_devolver_relatorio_ajuste(self.operador, self.relatorio))

        self.negar(self.operador, CodigoPermissao.RELATORIOS_APROVAR)
        self.assertFalse(usuario_pode_aprovar_relatorio(self.operador, self.relatorio))

        self.permitir(self.operador, CodigoPermissao.RELATORIOS_REJEITAR)
        self.assertTrue(usuario_pode_rejeitar_relatorio(self.operador, self.relatorio))

        self.permitir(self.operador, CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE)
        self.assertTrue(usuario_pode_devolver_relatorio_ajuste(self.operador, self.relatorio))

    def test_acao_alheia_exige_visualizacao_universal(self):
        self.permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self.permitir(self.operador, CodigoPermissao.RELATORIOS_APROVAR)

        self.assertFalse(usuario_pode_aprovar_relatorio(self.operador, self.relatorio))

        self.permitir(self.operador, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.assertTrue(usuario_pode_aprovar_relatorio(self.operador, self.relatorio))

    def test_botoes_do_detail_acompanham_permissoes_independentes(self):
        self.permitir(self.operador, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self.permitir(self.operador, CodigoPermissao.RELATORIOS_DEVOLVER_AJUSTE)
        self.client.force_login(self.operador)

        resposta = self.client.get(reverse("relatorios:relatorio_detail", args=[self.relatorio.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'data-tour="detail-aprovar-relatorio"')
        self.assertContains(resposta, 'data-tour="detail-solicitar-ajuste"')
        self.assertNotContains(resposta, 'data-tour="detail-rejeitar-relatorio"')

    def test_post_manual_sem_permissao_especifica_e_bloqueado(self):
        self.permitir(self.operador, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self.permitir(self.operador, CodigoPermissao.FINANCEIRO_ACESSAR)
        self.client.force_login(self.operador)

        resposta = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                args=[self.relatorio.pk, StatusRelatorio.APROVADO],
            )
        )
        self.relatorio.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.relatorio.status, StatusRelatorio.CONFERENCIA)

    def test_aprovar_isolado_nao_altera_valores_por_post_adulterado(self):
        self.preparar_usuario_acao_alheia(self.operador, CodigoPermissao.RELATORIOS_APROVAR)
        self.client.force_login(self.operador)

        resposta = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                args=[self.relatorio.pk, StatusRelatorio.APROVADO],
            ),
            {f"despesa_{self.despesa.pk}_valor_aprovado": "10.00"},
        )
        self.relatorio.refresh_from_db()
        self.despesa.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.relatorio.status, StatusRelatorio.APROVADO)
        self.assertEqual(self.despesa.valor_aprovado, Decimal("50.00"))


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=True,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class PermissoesCentralMotorTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.dono = self.User.objects.create_user(username="dono")
        self.outro = self.User.objects.create_user(username="outro")

    def _relatorio(self, status, criado_por):
        return SimpleNamespace(
            pk=99,
            status=status,
            criado_por_id=criado_por.pk,
        )

    def _permitir(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.dono,
        )

    def _negar(self, usuario, codigo):
        definir_override_permissao(
            usuario,
            codigo,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.dono,
        )

    def test_full_access_exclusivo_por_configuracao(self):
        usuario = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        relatorio = self._relatorio(StatusRelatorio.CONFERENCIA, self.outro)

        self.assertTrue(usuario_tem_full_access_erp(usuario))
        self.assertTrue(
            usuario_tem_permissao_central(
                usuario, CodigoPermissao.RELATORIOS_APROVAR, objeto=relatorio
            )
        )
        self.assertTrue(usuario_tem_permissao_central(usuario, CodigoPermissao.MANUTENCAO_ACESSAR))

    def test_domain_admin_nao_recebe_full_access_no_motor_central(self):
        grupo = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        usuario = self.User.objects.create_user(username="domain.admin")
        usuario.groups.add(grupo)

        self.assertFalse(usuario_tem_full_access_erp(usuario))
        self.assertFalse(usuario_tem_permissao_central(usuario, CodigoPermissao.MANUTENCAO_ACESSAR))

    def test_superuser_nao_recebe_full_access_de_negocio(self):
        usuario = self.User.objects.create_superuser(
            username="superuser.erp",
            email="superuser@example.com",
            password="x",
        )

        self.assertFalse(usuario_tem_full_access_erp(usuario))
        self.assertFalse(usuario_tem_permissao_central(usuario, CodigoPermissao.FINANCEIRO_ACESSAR))

    @override_settings(EXTRA_ADMIN_USERS=["extra.admin"])
    def test_extra_admin_users_nao_recebe_full_access_central(self):
        usuario = self.User.objects.create_user(username="extra.admin")

        self.assertFalse(usuario_tem_full_access_erp(usuario))
        self.assertFalse(usuario_tem_permissao_central(usuario, CodigoPermissao.MANUTENCAO_ACESSAR))

    def test_usuario_normal_visualiza_relatorio_proprio(self):
        relatorio = self._relatorio(StatusRelatorio.APROVADO, self.dono)

        self.assertTrue(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR, objeto=relatorio
            )
        )

    def test_usuario_normal_nao_visualiza_relatorio_alheio_aprovado(self):
        relatorio = self._relatorio(StatusRelatorio.APROVADO, self.outro)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR, objeto=relatorio
            )
        )

    def test_visualizacao_universal_permite_ver_relatorio_alheio(self):
        relatorio = self._relatorio(StatusRelatorio.APROVADO, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)

        self.assertTrue(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR, objeto=relatorio
            )
        )

    def test_visualizacao_universal_sozinha_nao_edita_alheio(self):
        relatorio = self._relatorio(StatusRelatorio.RASCUNHO, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio
            )
        )

    def test_editar_alheios_exige_visualizacao_universal(self):
        relatorio = self._relatorio(StatusRelatorio.RASCUNHO, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio
            )
        )

    def test_editar_alheios_respeita_workflow(self):
        relatorio_aprovado = self._relatorio(StatusRelatorio.APROVADO, self.outro)
        relatorio_conferencia = self._relatorio(StatusRelatorio.CONFERENCIA, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio_aprovado
            )
        )
        self.assertTrue(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_EDITAR, objeto=relatorio_conferencia
            )
        )

    def test_aprovar_respeita_status_conferencia(self):
        relatorio_rascunho = self._relatorio(StatusRelatorio.RASCUNHO, self.outro)
        relatorio_conferencia = self._relatorio(StatusRelatorio.CONFERENCIA, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(self.dono, CodigoPermissao.FINANCEIRO_ACESSAR)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_APROVAR)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_APROVAR, objeto=relatorio_rascunho
            )
        )
        self.assertTrue(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_APROVAR, objeto=relatorio_conferencia
            )
        )

    def test_aprovar_relatorio_alheio_exige_visualizacao_universal(self):
        relatorio = self._relatorio(StatusRelatorio.CONFERENCIA, self.outro)
        self._permitir(self.dono, CodigoPermissao.FINANCEIRO_ACESSAR)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_APROVAR)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_APROVAR, objeto=relatorio
            )
        )

    def test_override_negar_vence_default_comum(self):
        grupo = Group.objects.create(name=GRUPO_TECNICO)
        self.dono.groups.add(grupo)
        self.assertTrue(usuario_tem_permissao_central(self.dono, CodigoPermissao.ERP_ACESSAR))

        self._negar(self.dono, CodigoPermissao.ERP_ACESSAR)

        self.assertFalse(usuario_tem_permissao_central(self.dono, CodigoPermissao.ERP_ACESSAR))

    def test_override_permitir_nao_ignora_workflow(self):
        relatorio = self._relatorio(StatusRelatorio.RASCUNHO, self.outro)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS)
        self._permitir(self.dono, CodigoPermissao.FINANCEIRO_ACESSAR)
        self._permitir(self.dono, CodigoPermissao.RELATORIOS_APROVAR)

        self.assertFalse(
            usuario_tem_permissao_central(
                self.dono, CodigoPermissao.RELATORIOS_APROVAR, objeto=relatorio
            )
        )

    def test_codigo_desconhecido_falha_fechado(self):
        self.assertFalse(usuario_tem_permissao_central(self.dono, "codigo.desconhecido"))

    def test_objeto_obrigatorio_ausente_falha_fechado(self):
        self.assertFalse(
            usuario_tem_permissao_central(self.dono, CodigoPermissao.RELATORIOS_EDITAR)
        )

    def test_grupo_socios_nao_recebe_full_access_por_grupo(self):
        grupo = Group.objects.create(name="Socios")
        usuario = self.User.objects.create_user(username="socio.erp")
        usuario.groups.add(grupo)

        self.assertFalse(usuario_tem_full_access_erp(usuario))
        self.assertFalse(usuario_tem_permissao_central(usuario, CodigoPermissao.MANUTENCAO_ACESSAR))


class PermissoesOverrideHistoricoTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.usuario = self.User.objects.create_user(username="usuario.permissao")
        self.responsavel = self.User.objects.create_user(username="responsavel.permissao")

    def test_historico_registra_permitir_negar_e_herdar(self):
        definir_override_permissao(
            self.usuario,
            CodigoPermissao.RELATORIOS_APROVAR,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.responsavel,
        )
        definir_override_permissao(
            self.usuario,
            CodigoPermissao.RELATORIOS_APROVAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.responsavel,
        )
        definir_override_permissao(
            self.usuario,
            CodigoPermissao.RELATORIOS_APROVAR,
            EstadoHistoricoPermissao.HERDAR,
            alterado_por=self.responsavel,
        )

        self.assertEqual(
            estado_efetivo_override(self.usuario, CodigoPermissao.RELATORIOS_APROVAR),
            EstadoHistoricoPermissao.HERDAR,
        )
        self.assertFalse(
            PermissaoUsuarioOverride.objects.filter(
                usuario=self.usuario,
                codigo=CodigoPermissao.RELATORIOS_APROVAR,
            ).exists()
        )
        historico = list(
            HistoricoPermissaoUsuario.objects.filter(
                usuario_afetado=self.usuario,
                codigo=CodigoPermissao.RELATORIOS_APROVAR,
            ).order_by("id")
        )
        self.assertEqual(len(historico), 3)
        self.assertEqual(historico[0].estado_anterior, EstadoHistoricoPermissao.HERDAR)
        self.assertEqual(historico[0].estado_novo, EstadoPermissaoUsuario.PERMITIR)
        self.assertEqual(historico[1].estado_anterior, EstadoPermissaoUsuario.PERMITIR)
        self.assertEqual(historico[1].estado_novo, EstadoPermissaoUsuario.NEGAR)
        self.assertEqual(historico[2].estado_anterior, EstadoPermissaoUsuario.NEGAR)
        self.assertEqual(historico[2].estado_novo, EstadoHistoricoPermissao.HERDAR)
        self.assertTrue(all(item.alterado_por_id == self.responsavel.pk for item in historico))

    def test_override_e_historico_sao_atomicos(self):
        with patch(
            "relatorios.models.HistoricoPermissaoUsuario.objects.create",
            side_effect=RuntimeError("falha auditoria"),
        ):
            with self.assertRaises(RuntimeError):
                definir_override_permissao(
                    self.usuario,
                    CodigoPermissao.RELATORIOS_APROVAR,
                    EstadoPermissaoUsuario.PERMITIR,
                    alterado_por=self.responsavel,
                )

        self.assertFalse(
            PermissaoUsuarioOverride.objects.filter(
                usuario=self.usuario,
                codigo=CodigoPermissao.RELATORIOS_APROVAR,
            ).exists()
        )


@override_settings(
    PERMISSOES_CENTRAL_ENABLED=False,
    ERP_FULL_ACCESS_USERS=["CONTROL\\gabriel.oliveira"],
    EXTRA_ADMIN_USERS=[],
)
class CentralUsuariosPermissoesViewsTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.full = self.User.objects.create_user(username="CONTROL\\gabriel.oliveira")
        self.alvo = self.User.objects.create_user(
            username="maria.silva",
            first_name="Maria",
            last_name="Silva",
            email="maria@example.com",
        )
        self.comum = self.User.objects.create_user(username="usuario.comum")

    def test_full_access_abre_central(self):
        self.client.force_login(self.full)

        response = self.client.get(reverse("relatorios:usuarios_central_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central em preparacao")
        self.assertContains(response, "maria.silva")

    def test_usuario_comum_nao_abre_central(self):
        self.client.force_login(self.comum)

        response = self.client.get(reverse("relatorios:usuarios_central_list"))

        self.assertEqual(response.status_code, 403)

    def test_domain_admin_nao_abre_apenas_por_grupo(self):
        grupo = Group.objects.create(name=GRUPO_DOMAIN_ADMINS)
        self.comum.groups.add(grupo)
        self.client.force_login(self.comum)

        response = self.client.get(reverse("relatorios:usuarios_central_list"))

        self.assertEqual(response.status_code, 403)

    def test_superuser_nao_abre_apenas_por_superuser(self):
        superuser = self.User.objects.create_superuser(
            username="superuser.local",
            email="super@example.com",
            password="x",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("relatorios:usuarios_central_list"))

        self.assertEqual(response.status_code, 403)

    def test_listagem_nao_expoe_senha(self):
        self.client.force_login(self.full)

        response = self.client.get(reverse("relatorios:usuarios_central_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.alvo.password)
        self.assertNotContains(response, "password")

    def test_cria_permitir_negar_e_herdar_com_historico(self):
        self.client.force_login(self.full)
        url = reverse("relatorios:usuario_permissao_override", args=[self.alvo.pk])

        response = self.client.post(
            url,
            {
                "codigo": CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                "estado": EstadoPermissaoUsuario.PERMITIR,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PermissaoUsuarioOverride.objects.filter(
                usuario=self.alvo,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                estado=EstadoPermissaoUsuario.PERMITIR,
            ).exists()
        )

        self.client.post(
            url,
            {
                "codigo": CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                "estado": EstadoPermissaoUsuario.NEGAR,
            },
        )
        self.assertEqual(
            PermissaoUsuarioOverride.objects.get(
                usuario=self.alvo,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            ).estado,
            EstadoPermissaoUsuario.NEGAR,
        )

        self.client.post(
            url,
            {
                "codigo": CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                "estado": EstadoHistoricoPermissao.HERDAR,
            },
        )
        self.assertFalse(
            PermissaoUsuarioOverride.objects.filter(
                usuario=self.alvo,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            ).exists()
        )
        self.assertEqual(
            HistoricoPermissaoUsuario.objects.filter(
                usuario_afetado=self.alvo,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            ).count(),
            3,
        )

    def test_codigo_invalido_falha_sem_override(self):
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissao_override", args=[self.alvo.pk]),
            {"codigo": "codigo.invalido", "estado": EstadoPermissaoUsuario.PERMITIR},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.alvo).exists())

    def test_usuario_alvo_full_nao_recebe_override(self):
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissao_override", args=[self.full.pk]),
            {
                "codigo": CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                "estado": EstadoPermissaoUsuario.NEGAR,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.full).exists())

    def test_editar_alheios_sem_visualizacao_universal_e_rejeitado(self):
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissao_override", args=[self.alvo.pk]),
            {
                "codigo": CodigoPermissao.RELATORIOS_EDITAR_ALHEIOS,
                "estado": EstadoPermissaoUsuario.PERMITIR,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.alvo).exists())

    def test_csrf_nao_enfraquecido_no_post(self):
        client = DjangoClient(enforce_csrf_checks=True)
        client.force_login(self.full)

        response = client.post(
            reverse("relatorios:usuario_permissao_override", args=[self.alvo.pk]),
            {
                "codigo": CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                "estado": EstadoPermissaoUsuario.PERMITIR,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.alvo).exists())

    def test_sidebar_contexto_legado_flag_off_expoe_usuarios_somente_para_full(self):
        request_full = RequestFactory().get("/")
        request_full.user = self.full
        request_comum = RequestFactory().get("/")
        request_comum.user = self.comum

        self.assertTrue(permissoes_erp(request_full)["permissoes_erp"]["usuarios_gerenciar"])
        self.assertFalse(permissoes_erp(request_comum)["permissoes_erp"]["usuarios_gerenciar"])
        self.assertFalse(getattr(settings, "PERMISSOES_CENTRAL_ENABLED", True))

    @override_settings(PERMISSOES_CENTRAL_ENABLED=True)
    def test_flag_on_permite_central_por_permissoes_gerenciar(self):
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.PERMISSOES_GERENCIAR,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

        self.assertTrue(usuario_pode_acessar_central_permissoes(self.alvo))

    def test_replicacao_preview_nao_grava(self):
        destino = self.User.objects.create_user(username="destino.preview")
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "preview",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino).exists())
        self.assertEqual(HistoricoPermissaoUsuario.objects.filter(usuario_afetado=destino).count(), 0)
        self.assertContains(response, "Preview da replicacao")

    def test_usuario_comum_nao_replica_permissoes(self):
        destino = self.User.objects.create_user(username="destino.negado")
        self.client.force_login(self.comum)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino).exists())

    def test_replicacao_csrf_nao_enfraquecido(self):
        destino = self.User.objects.create_user(username="destino.csrf")
        client = DjangoClient(enforce_csrf_checks=True)
        client.force_login(self.full)

        response = client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino).exists())

    def test_replicacao_somente_overrides_nao_altera_herdar_da_fonte(self):
        destino = self.User.objects.create_user(username="destino.overrides")
        definir_override_permissao(
            destino,
            CodigoPermissao.FINANCEIRO_ACESSAR,
            EstadoPermissaoUsuario.NEGAR,
            alterado_por=self.full,
        )
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [
                    CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                    CodigoPermissao.FINANCEIRO_ACESSAR,
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PermissaoUsuarioOverride.objects.get(
                usuario=destino,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            ).estado,
            EstadoPermissaoUsuario.PERMITIR,
        )
        self.assertEqual(
            PermissaoUsuarioOverride.objects.get(
                usuario=destino,
                codigo=CodigoPermissao.FINANCEIRO_ACESSAR,
            ).estado,
            EstadoPermissaoUsuario.NEGAR,
        )

    def test_replicacao_exata_remove_override_quando_fonte_herda(self):
        destino = self.User.objects.create_user(username="destino.exato")
        definir_override_permissao(
            destino,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "exato",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PermissaoUsuarioOverride.objects.filter(
                usuario=destino,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            ).exists()
        )
        self.assertEqual(
            HistoricoPermissaoUsuario.objects.filter(
                usuario_afetado=destino,
                codigo=CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
                estado_novo=EstadoHistoricoPermissao.HERDAR,
            ).count(),
            1,
        )

    def test_replicacao_nao_aceita_full_como_fonte_ou_destino(self):
        destino = self.User.objects.create_user(username="destino.full")
        self.client.force_login(self.full)

        response_fonte = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.full.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )
        response_destino = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(self.full.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response_fonte.status_code, 200)
        self.assertEqual(response_destino.status_code, 200)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino).exists())
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.full).exists())

    def test_replicacao_nao_aceita_fonte_como_destino(self):
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(self.alvo.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=self.alvo).exists())

    def test_replicacao_com_dependencia_invalida_e_rejeitada(self):
        destino = self.User.objects.create_user(username="destino.dep")
        PermissaoUsuarioOverride.objects.create(
            usuario=self.alvo,
            codigo=CodigoPermissao.RELATORIOS_APROVAR,
            estado=EstadoPermissaoUsuario.PERMITIR,
            atualizado_por=self.full,
        )
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_APROVAR],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "requer")
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino).exists())

    def test_replicacao_atomica_nao_aplica_destino_valido_se_lote_tem_erro(self):
        destino_valido = self.User.objects.create_user(username="destino.valido")
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino_valido.pk), str(self.full.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PermissaoUsuarioOverride.objects.filter(usuario=destino_valido).exists())

    def test_replicacao_itens_iguais_nao_geram_historico(self):
        destino = self.User.objects.create_user(username="destino.igual")
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        definir_override_permissao(
            destino,
            CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )
        historico_antes = HistoricoPermissaoUsuario.objects.filter(usuario_afetado=destino).count()
        self.client.force_login(self.full)

        response = self.client.post(
            reverse("relatorios:usuario_permissoes_replicar", args=[self.alvo.pk]),
            {
                "replicacao_acao": "aplicar",
                "modo": "overrides",
                "destinos": [str(destino.pk)],
                "codigos": [CodigoPermissao.RELATORIOS_VISUALIZAR_ALHEIOS],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HistoricoPermissaoUsuario.objects.filter(usuario_afetado=destino).count(), historico_antes)

    def test_replicacao_critica_aparece_no_preview(self):
        destino = self.User.objects.create_user(username="destino.critica")
        definir_override_permissao(
            self.alvo,
            CodigoPermissao.RELATORIOS_REABRIR,
            EstadoPermissaoUsuario.PERMITIR,
            alterado_por=self.full,
        )

        preview = preparar_preview_replicacao_permissoes(
            administrador=self.full,
            usuario_fonte=self.alvo,
            destino_ids=[destino.pk],
            codigos=[CodigoPermissao.RELATORIOS_REABRIR],
            modo="overrides",
        )

        self.assertEqual(preview.criticas, 1)
        self.assertEqual(preview.total_alteracoes, 1)


class HospedagemPeriodoTests(TestCase):
    def test_calcula_diarias_por_periodo(self):
        self.assertEqual(
            calcular_diarias_periodo(date(2026, 8, 1), date(2026, 8, 8)),
            7,
        )
        self.assertEqual(
            calcular_diarias_periodo(date(2026, 8, 1), date(2026, 8, 2)),
            1,
        )

    def test_limite_politica_hospedagem_multiplica_diarias(self):
        usuario = get_user_model().objects.create_user("hospedagem")
        tecnico = Tecnico.objects.create(nome="Tecnico Hospedagem", usuario=usuario)
        cliente = Cliente.objects.create(nome="Cliente Hospedagem", cidade="Curitiba", uf="PR")
        relatorio = RelatorioTecnico.objects.create(
            numero=900101,
            cliente=cliente,
            tecnico_responsavel=tecnico,
            cidade_atendimento="Curitiba",
            uf_atendimento="PR",
            tipo_localidade="capital",
            data_inicio=date(2026, 8, 1),
            data_fim=date(2026, 8, 8),
            motivo="Hospedagem",
        )
        PoliticaValor.objects.create(
            chave="HOSPEDAGEM_CURITIBA",
            tipo_politica="hospedagem",
            tipo_despesa=TipoDespesa.HOSPEDAGEM,
            cidade="Curitiba",
            descricao="Hospedagem Curitiba",
            limite_valor=Decimal("250.00"),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )
        despesa = ItemDespesa(
            relatorio=relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.HOSPEDAGEM,
            descricao="Hotel Curitiba",
            valor=Decimal("1620.00"),
            data_inicio_hospedagem=date(2026, 8, 1),
            data_fim_hospedagem=date(2026, 8, 8),
        )

        self.assertEqual(despesa.quantidade_diarias_hospedagem, 7)
        self.assertEqual(despesa.valor_politica_diaria, Decimal("250.00"))
        self.assertEqual(despesa.valor_politica, Decimal("1750.00"))
        self.assertFalse(despesa.acima_politica)


class PoliticaValorEscopoEmpresaTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user("politica-empresa")
        self.tecnico = Tecnico.objects.create(nome="Tecnico Politica", usuario=self.usuario)
        self.cliente = Cliente.objects.create(nome="Cliente Politica")
        self.relatorio = RelatorioTecnico.objects.create(
            numero=900150,
            cliente=self.cliente,
            tecnico_responsavel=self.tecnico,
            cidade_atendimento="Curitiba",
            uf_atendimento="PR",
            tipo_localidade="capital",
            data_inicio=date(2026, 8, 1),
            data_fim=date(2026, 8, 2),
            motivo="Atendimento",
        )

    def criar_politica(
        self,
        valor,
        *,
        chave="REFEICAO_CAPITAL",
        escopo=EscopoPoliticaValor.GLOBAL,
        empresas=(),
        inicio=date(2026, 1, 1),
        fim=None,
        ativo=True,
    ):
        politica = PoliticaValor.objects.create(
            chave=chave,
            escopo=escopo,
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO
            if chave.startswith("REFEICAO")
            else PoliticaValor.TipoPolitica.VALOR_KM,
            tipo_despesa=TipoDespesa.ALIMENTACAO if chave.startswith("REFEICAO") else "",
            tipo_localidade="capital" if chave.startswith("REFEICAO") else "",
            descricao=chave,
            limite_valor=Decimal(valor) if chave.startswith("REFEICAO") else None,
            valor_km=Decimal(valor) if chave == "VALOR_KM_CONTROLSUL" else None,
            vigencia_inicio=inicio,
            vigencia_fim=fim,
            ativo=ativo,
        )
        for empresa in empresas:
            PoliticaValorEmpresaGrupo.objects.create(
                politica=politica,
                empresa_grupo=empresa,
            )
        return politica

    def resolver_refeicao(self, empresa_grupo=None, data_ref=date(2026, 8, 1)):
        return resolver_politica_despesa(
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            data=data_ref,
            tipo_localidade="capital",
            valor_informado=Decimal("100.00"),
            empresa_grupo=empresa_grupo,
        )

    def test_politica_global_continua_resolvendo_com_e_sem_empresa(self):
        self.criar_politica("80.00")

        self.assertEqual(self.resolver_refeicao().valor, Decimal("80.00"))
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL).valor,
            Decimal("80.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.FISCALMAX).valor,
            Decimal("80.00"),
        )

    def test_politica_especifica_tem_precedencia_e_global_faz_fallback(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL).valor,
            Decimal("90.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.FISCALMAX).valor,
            Decimal("80.00"),
        )
        self.assertEqual(self.resolver_refeicao().valor, Decimal("80.00"))

    def test_politica_especifica_pode_valer_para_varias_empresas(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "95.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL, EmpresaGrupo.FISCALMAX],
        )

        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL).valor,
            Decimal("95.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.FISCALMAX).valor,
            Decimal("95.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.BLAZIUS_E_LORENZETTI).valor,
            Decimal("80.00"),
        )

    def test_politica_especifica_sem_global_nao_vira_zero_para_outras_empresas(self):
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL).valor,
            Decimal("90.00"),
        )
        self.assertIsNone(self.resolver_refeicao(EmpresaGrupo.FISCALMAX))
        self.assertIsNone(self.resolver_refeicao())

        despesa = ItemDespesa.objects.create(
            relatorio=self.relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco",
            valor=Decimal("100.00"),
        )
        aplicar_politica_valor_aprovado_inicial(despesa)
        despesa.refresh_from_db()

        self.assertIsNone(despesa.valor_politica)
        self.assertIsNone(despesa.valor_aprovado)
        self.assertEqual(despesa.valor_final, Decimal("100.00"))

    def test_casa_chico_nao_usa_fallback_global_de_politicas(self):
        self.criar_politica("80.00")

        self.assertIsNone(self.resolver_refeicao(EmpresaGrupo.CASA_CHICO_DE_PNEUS))

    def test_casa_chico_pode_usar_politica_especifica_sem_global(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "70.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CASA_CHICO_DE_PNEUS],
        )

        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CASA_CHICO_DE_PNEUS).valor,
            Decimal("70.00"),
        )

    def test_fallback_respeita_vigencia_da_politica_especifica(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
            inicio=date(2026, 9, 1),
        )
        self.criar_politica(
            "85.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.FISCALMAX],
            inicio=date(2026, 1, 1),
            fim=date(2026, 7, 31),
        )

        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL).valor,
            Decimal("80.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.FISCALMAX).valor,
            Decimal("80.00"),
        )
        self.assertEqual(
            self.resolver_refeicao(EmpresaGrupo.CONTROLSUL, date(2026, 9, 15)).valor,
            Decimal("90.00"),
        )

    def test_global_futura_ou_encerrada_nao_resolve_sem_empresa(self):
        self.criar_politica("80.00", inicio=date(2026, 9, 1))
        self.criar_politica(
            "75.00",
            inicio=date(2026, 1, 1),
            fim=date(2026, 7, 31),
        )

        self.assertIsNone(self.resolver_refeicao())

    def test_validacao_rejeita_especifica_sobreposta_para_mesma_empresa(self):
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )
        nova = PoliticaValor(
            chave="REFEICAO_CAPITAL",
            escopo=EscopoPoliticaValor.EMPRESAS,
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeicao Capital nova",
            limite_valor=Decimal("95.00"),
            vigencia_inicio=date(2026, 6, 1),
            ativo=True,
        )

        with self.assertRaises(ValidationError):
            validar_configuracao_politica_empresas(
                nova,
                [EmpresaGrupo.CONTROLSUL],
            )

    def test_validacao_permite_global_com_especifica_e_empresas_distintas(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )
        fiscalmax = PoliticaValor(
            chave="REFEICAO_CAPITAL",
            escopo=EscopoPoliticaValor.EMPRESAS,
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeicao FiscalMax",
            limite_valor=Decimal("92.00"),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )

        validar_configuracao_politica_empresas(
            fiscalmax,
            [EmpresaGrupo.FISCALMAX],
        )

    def test_item_despesa_usa_empresa_grupo_do_relatorio(self):
        self.criar_politica("80.00")
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )
        self.relatorio.empresa_grupo = EmpresaGrupo.CONTROLSUL
        self.relatorio.save(update_fields=["empresa_grupo"])
        despesa = ItemDespesa.objects.create(
            relatorio=self.relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco",
            valor=Decimal("100.00"),
        )

        aplicar_politica_valor_aprovado_inicial(despesa)
        despesa.refresh_from_db()

        self.assertEqual(despesa.valor_politica, Decimal("90.00"))
        self.assertEqual(despesa.valor_aprovado, Decimal("90.00"))

    def test_empresa_nao_altera_multiplicador_por_tecnicos(self):
        self.criar_politica(
            "90.00",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )
        self.relatorio.empresa_grupo = EmpresaGrupo.CONTROLSUL
        self.relatorio.save(update_fields=["empresa_grupo"])
        tecnico_extra = Tecnico.objects.create(nome="Tecnico Extra Politica")
        RelatorioTecnicoEquipe.objects.create(
            relatorio=self.relatorio,
            tecnico=tecnico_extra,
        )
        despesa = ItemDespesa.objects.create(
            relatorio=self.relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco equipe",
            valor=Decimal("200.00"),
        )
        sync_tecnicos_despesa(
            despesa,
            [self.tecnico.pk, tecnico_extra.pk],
            self.usuario,
        )

        aplicar_politica_valor_aprovado_inicial(despesa)
        despesa.refresh_from_db()

        self.assertEqual(despesa.valor_politica, Decimal("180.00"))
        self.assertEqual(despesa.valor_aprovado, Decimal("180.00"))

    def test_valor_km_control_sul_continua_global_para_chamada_antiga(self):
        self.criar_politica(
            "1.3500",
            chave="VALOR_KM_CONTROLSUL",
            escopo=EscopoPoliticaValor.GLOBAL,
        )
        self.criar_politica(
            "2.0000",
            chave="VALOR_KM_CONTROLSUL",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        self.assertEqual(valor_km_control_sul(date(2026, 8, 1)), Decimal("1.35"))

    def test_casa_chico_nao_altera_valor_km_reembolso_tecnico(self):
        self.criar_politica(
            "1.3500",
            chave="VALOR_KM_CONTROLSUL",
            escopo=EscopoPoliticaValor.GLOBAL,
        )
        empresa = Cliente.objects.create(
            nome="CASA CHICO DE PNEUS LTDA",
            valor_km=Decimal("9.9900"),
        )

        calculo = calcular_km_financeiro(Decimal("10.00"), empresa)

        self.assertEqual(calculo["valor_km_reembolso_tecnico"], Decimal("1.3500"))
        self.assertEqual(calculo["valor_km_cliente"], Decimal("1.3500"))
        self.assertEqual(calculo["valor_reembolso_tecnico"], Decimal("13.50"))


class ManutencaoPoliticasViewsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="admin.politicas",
            email="admin@example.com",
            password="senha",
        )
        self.comum = get_user_model().objects.create_user(
            username="usuario.comum",
            password="senha",
        )

    def criar_politica(
        self,
        chave="TESTE_REFEICAO_CAPITAL",
        *,
        escopo=EscopoPoliticaValor.GLOBAL,
        empresas=(),
        limite=Decimal("80.00"),
        inicio=date(2026, 1, 1),
        fim=None,
        ativo=True,
    ):
        politica = PoliticaValor.objects.create(
            chave=chave,
            escopo=escopo,
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao=chave,
            limite_valor=limite,
            vigencia_inicio=inicio,
            vigencia_fim=fim,
            ativo=ativo,
        )
        for empresa in empresas:
            PoliticaValorEmpresaGrupo.objects.create(
                politica=politica,
                empresa_grupo=empresa,
            )
        return politica

    def dados_post(self, **overrides):
        dados = {
            "chave": "TESTE_NOVA_POLITICA",
            "descricao": "Teste nova politica",
            "tipo_politica": PoliticaValor.TipoPolitica.REFEICAO,
            "tipo_despesa": TipoDespesa.ALIMENTACAO,
            "tipo_localidade": "capital",
            "cidade": "",
            "origem": "",
            "destino": "",
            "limite_valor": "80.00",
            "valor_km": "",
            "vigencia_inicio": "2026-01-01",
            "vigencia_fim": "",
            "ativo": "on",
            "escopo": EscopoPoliticaValor.GLOBAL,
            "empresas": [],
        }
        dados.update(overrides)
        return dados

    def test_acesso_nao_autorizado_recebe_403(self):
        self.client.login(username="usuario.comum", password="senha")

        response = self.client.get(reverse("relatorios:manutencao_politicas"))

        self.assertEqual(response.status_code, 403)

    def test_listagem_exibe_politica_global_e_nao_exibe_exclusao(self):
        self.client.login(username="admin.politicas", password="senha")
        self.criar_politica()

        response = self.client.get(reverse("relatorios:manutencao_politicas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TESTE_REFEICAO_CAPITAL")
        self.assertContains(response, "GLOBAL")
        self.assertNotContains(response, "Excluir")

    def test_listagem_exibe_politica_empresarial_com_empresas(self):
        self.client.login(username="admin.politicas", password="senha")
        self.criar_politica(
            chave="TESTE_EMPRESARIAL",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL, EmpresaGrupo.FISCALMAX],
        )

        response = self.client.get(reverse("relatorios:manutencao_politicas"))

        self.assertContains(response, "EMPRESAS")
        self.assertContains(response, "CONTROLSUL")
        self.assertContains(response, "FISCALMAX")

    def test_filtros_funcionam_server_side(self):
        self.client.login(username="admin.politicas", password="senha")
        self.criar_politica(chave="TESTE_GLOBAL")
        self.criar_politica(
            chave="TESTE_CONTROLSUL",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        response = self.client.get(
            reverse("relatorios:manutencao_politicas"),
            {"escopo": EscopoPoliticaValor.EMPRESAS, "empresa": EmpresaGrupo.CONTROLSUL},
        )

        self.assertContains(response, "TESTE_CONTROLSUL")
        self.assertNotContains(response, "TESTE_GLOBAL")

    def test_criacao_global_nao_cria_vinculos_empresariais(self):
        self.client.login(username="admin.politicas", password="senha")

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(chave="TESTE_GLOBAL_CRIADA"),
        )

        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        politica = PoliticaValor.objects.get(chave="TESTE_GLOBAL_CRIADA")
        self.assertEqual(politica.escopo, EscopoPoliticaValor.GLOBAL)
        self.assertFalse(politica.empresas_grupo.exists())

    def test_criacao_empresas_exige_empresa(self):
        self.client.login(username="admin.politicas", password="senha")

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(escopo=EscopoPoliticaValor.EMPRESAS, empresas=[]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe ao menos uma empresa")
        self.assertFalse(PoliticaValor.objects.filter(chave="TESTE_NOVA_POLITICA").exists())

    def test_criacao_empresas_com_multiplas_empresas(self):
        self.client.login(username="admin.politicas", password="senha")

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(
                chave="TESTE_MULTI_EMPRESA",
                escopo=EscopoPoliticaValor.EMPRESAS,
                empresas=[EmpresaGrupo.CONTROLSUL, EmpresaGrupo.FISCALMAX],
            ),
        )

        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        politica = PoliticaValor.objects.get(chave="TESTE_MULTI_EMPRESA")
        self.assertEqual(
            set(politica.empresas_grupo.values_list("empresa_grupo", flat=True)),
            {EmpresaGrupo.CONTROLSUL, EmpresaGrupo.FISCALMAX},
        )

    def test_conflito_empresarial_invalida_criacao_mas_global_coexiste(self):
        self.client.login(username="admin.politicas", password="senha")
        self.criar_politica(
            chave="TESTE_CONFLITO",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(
                chave="TESTE_CONFLITO",
                escopo=EscopoPoliticaValor.EMPRESAS,
                empresas=[EmpresaGrupo.CONTROLSUL],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sobreposta")

        response_global = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(chave="TESTE_CONFLITO"),
        )
        self.assertRedirects(response_global, reverse("relatorios:manutencao_politicas"))

    def test_edicao_preserva_id_e_altera_global_para_empresas(self):
        self.client.login(username="admin.politicas", password="senha")
        politica = self.criar_politica(chave="TESTE_EDITA")
        politica_id = politica.pk

        response = self.client.post(
            reverse("relatorios:manutencao_politica_editar", args=[politica.pk]),
            self.dados_post(
                chave="TESTE_EDITA",
                descricao="Teste editado",
                escopo=EscopoPoliticaValor.EMPRESAS,
                empresas=[EmpresaGrupo.CONTROLSUL],
            ),
        )

        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        politica.refresh_from_db()
        self.assertEqual(politica.pk, politica_id)
        self.assertEqual(politica.descricao, "Teste editado")
        self.assertEqual(politica.escopo, EscopoPoliticaValor.EMPRESAS)
        self.assertEqual(politica.empresas_grupo.count(), 1)

    def test_edicao_empresas_para_global_remove_vinculos(self):
        self.client.login(username="admin.politicas", password="senha")
        politica = self.criar_politica(
            chave="TESTE_REMOVE_EMPRESA",
            escopo=EscopoPoliticaValor.EMPRESAS,
            empresas=[EmpresaGrupo.CONTROLSUL],
        )

        response = self.client.post(
            reverse("relatorios:manutencao_politica_editar", args=[politica.pk]),
            self.dados_post(chave="TESTE_REMOVE_EMPRESA", escopo=EscopoPoliticaValor.GLOBAL),
        )

        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        politica.refresh_from_db()
        self.assertEqual(politica.escopo, EscopoPoliticaValor.GLOBAL)
        self.assertFalse(politica.empresas_grupo.exists())

    def test_duplicacao_abre_formulario_e_salvamento_cria_novo_id(self):
        self.client.login(username="admin.politicas", password="senha")
        politica = self.criar_politica(chave="TESTE_DUPLICAR")

        response = self.client.get(
            reverse("relatorios:manutencao_politica_duplicar", args=[politica.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TESTE_DUPLICAR")

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(chave="TESTE_DUPLICADA"),
        )
        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        self.assertNotEqual(
            PoliticaValor.objects.get(chave="TESTE_DUPLICADA").pk,
            politica.pk,
        )

    def test_encerramento_preenche_vigencia_fim(self):
        self.client.login(username="admin.politicas", password="senha")
        politica = self.criar_politica(chave="TESTE_ENCERRAR", fim=None)

        response = self.client.post(
            reverse("relatorios:manutencao_politica_encerrar", args=[politica.pk])
        )

        self.assertRedirects(response, reverse("relatorios:manutencao_politicas"))
        politica.refresh_from_db()
        self.assertEqual(politica.vigencia_fim, timezone.localdate())

    def test_posts_protegidos_por_autorizacao(self):
        self.client.login(username="usuario.comum", password="senha")

        response = self.client.post(
            reverse("relatorios:manutencao_politica_criar"),
            self.dados_post(chave="TESTE_NEGADO"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PoliticaValor.objects.filter(chave="TESTE_NEGADO").exists())


class DespesaTecnicoParticipanteTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user("tecnico-despesa")
        self.tecnico_a = Tecnico.objects.create(nome="Tecnico A", usuario=self.usuario)
        self.tecnico_b = Tecnico.objects.create(nome="Tecnico B")
        self.tecnico_c = Tecnico.objects.create(nome="Tecnico C")
        self.cliente = Cliente.objects.create(nome="Cliente Tecnicos")
        self.relatorio = RelatorioTecnico.objects.create(
            numero=900102,
            cliente=self.cliente,
            tecnico_responsavel=self.tecnico_a,
            cidade_atendimento="Curitiba",
            uf_atendimento="PR",
            tipo_localidade="capital",
            data_inicio=date(2026, 8, 1),
            data_fim=date(2026, 8, 2),
            motivo="Atendimento",
        )
        RelatorioTecnicoEquipe.objects.create(
            relatorio=self.relatorio,
            tecnico=self.tecnico_b,
        )
        self.despesa = ItemDespesa.objects.create(
            relatorio=self.relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoço equipe",
            valor=Decimal("420.00"),
        )

    def criar_politica_refeicao_capital(self, valor="80.00"):
        return PoliticaValor.objects.create(
            chave="REFEICAO_CAPITAL",
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeicao Capital",
            limite_valor=Decimal(valor),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )

    def criar_politica_hospedagem_sao_paulo(self, valor="900.00"):
        return PoliticaValor.objects.create(
            chave="HOSPEDAGEM_SAO_PAULO",
            tipo_politica=PoliticaValor.TipoPolitica.HOSPEDAGEM,
            tipo_despesa=TipoDespesa.HOSPEDAGEM,
            cidade="Sao Paulo",
            descricao="Hospedagem Sao Paulo",
            limite_valor=Decimal(valor),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )

    def usuario_financeiro(self):
        usuario = get_user_model().objects.create_user("financeiro-politica")
        grupo, _criado = Group.objects.get_or_create(name="Financeiro")
        usuario.groups.add(grupo)
        return usuario

    def preparar_relatorio_para_aprovacao(self):
        self.relatorio.status = StatusRelatorio.CONFERENCIA
        self.relatorio.tecnico_reembolso = self.tecnico_a
        self.relatorio.save(update_fields=["status", "tecnico_reembolso"])

    def test_sync_permite_multiplos_tecnicos_do_relatorio(self):
        erros = sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        self.assertEqual(erros, [])
        self.assertEqual(
            set(self.despesa.tecnicos_vinculados.values_list("tecnico_id", flat=True)),
            {self.tecnico_a.pk, self.tecnico_b.pk},
        )

    def test_politica_alimentacao_considera_tecnicos_participantes(self):
        PoliticaValor.objects.create(
            chave="REFEICAO_CAPITAL",
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeição Capital",
            limite_valor=Decimal("80.00"),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )
        self.despesa.valor = Decimal("146.00")
        self.despesa.save(update_fields=["valor"])

        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        self.assertEqual(self.despesa.quantidade_tecnicos_participantes, 2)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertEqual(self.despesa.excesso_politica, Decimal("0.00"))
        self.assertFalse(self.despesa.acima_politica)

    def test_aprovado_inicial_abaixo_da_politica_permanece_integral(self):
        self.criar_politica_refeicao_capital()
        self.despesa.valor = Decimal("100.00")
        self.despesa.save(update_fields=["valor"])
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        alterou = aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertFalse(alterou)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_final, Decimal("100.00"))

    def test_aprovado_inicial_no_limite_permanece_integral(self):
        self.criar_politica_refeicao_capital()
        self.despesa.valor = Decimal("160.00")
        self.despesa.save(update_fields=["valor"])
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        alterou = aplicar_politica_valor_aprovado_inicial(
            self.despesa,
            preservar_manual=True,
        )
        self.despesa.refresh_from_db()

        self.assertFalse(alterou)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_final, Decimal("160.00"))

    def test_aprovado_inicial_acima_da_politica_persiste_limite(self):
        self.criar_politica_refeicao_capital()
        self.despesa.valor = Decimal("200.00")
        self.despesa.save(update_fields=["valor"])
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        alterou = aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertTrue(alterou)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("160.00"))
        self.assertEqual(self.despesa.valor_final, Decimal("160.00"))

    def test_aprovado_inicial_considera_muitos_tecnicos_participantes(self):
        self.criar_politica_refeicao_capital()
        tecnicos = [self.tecnico_a, self.tecnico_b]
        for indice in range(6):
            tecnico = Tecnico.objects.create(nome=f"Tecnico Extra {indice}")
            RelatorioTecnicoEquipe.objects.create(
                relatorio=self.relatorio,
                tecnico=tecnico,
            )
            tecnicos.append(tecnico)
        self.despesa.valor = Decimal("800.00")
        self.despesa.save(update_fields=["valor"])
        sync_tecnicos_despesa(
            self.despesa,
            [tecnico.pk for tecnico in tecnicos],
            self.usuario,
        )

        aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertEqual(self.despesa.quantidade_tecnicos_participantes, 8)
        self.assertEqual(self.despesa.valor_politica, Decimal("640.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("640.00"))

    def test_aprovado_inicial_usa_tecnicos_da_propria_despesa(self):
        self.criar_politica_refeicao_capital()
        self.despesa.valor = Decimal("100.00")
        self.despesa.save(update_fields=["valor"])
        outra_despesa = ItemDespesa.objects.create(
            relatorio=self.relatorio,
            data=date(2026, 8, 1),
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Jantar individual",
            valor=Decimal("100.00"),
        )
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )
        sync_tecnicos_despesa(outra_despesa, [self.tecnico_a.pk], self.usuario)

        aplicar_politica_valor_aprovado_inicial(self.despesa)
        aplicar_politica_valor_aprovado_inicial(outra_despesa)
        self.despesa.refresh_from_db()
        outra_despesa.refresh_from_db()

        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertEqual(outra_despesa.valor_aprovado, Decimal("80.00"))
        self.assertEqual(outra_despesa.valor_politica, Decimal("80.00"))

    def test_aprovado_inicial_sem_politica_mantem_integral(self):
        self.despesa.tipo = TipoDespesa.PEDAGIO
        self.despesa.valor = Decimal("200.00")
        self.despesa.save(update_fields=["tipo", "valor"])
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        alterou = aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertFalse(alterou)
        self.assertIsNone(self.despesa.valor_politica)
        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_final, Decimal("200.00"))

    def test_aprovado_inicial_nao_converte_politica_ausente_em_zero(self):
        PoliticaValor.objects.create(
            chave="REFEICAO_CAPITAL",
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeicao Capital sem limite",
            limite_valor=None,
            valor_km=None,
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )
        self.despesa.valor = Decimal("90.00")
        self.despesa.save(update_fields=["valor"])
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)

        alterou = aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertIsNone(self.despesa.valor_politica)
        self.assertFalse(alterou)
        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_final, Decimal("90.00"))

    def test_aprovacao_aplica_politica_encontrada_mesmo_sem_input_no_post(self):
        self.criar_politica_refeicao_capital("80.00")
        self.despesa.valor = Decimal("90.20")
        self.despesa.valor_aprovado = None
        self.despesa.save(update_fields=["valor", "valor_aprovado"])
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)
        self.preparar_relatorio_para_aprovacao()

        aprovar_relatorio(self.relatorio.pk, self.usuario_financeiro(), post_data={})
        self.despesa.refresh_from_db()
        rateio = self.despesa.rateios.get(cliente=self.cliente)

        self.assertEqual(self.despesa.valor_politica, Decimal("80.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("80.00"))
        self.assertEqual(self.despesa.valor_final, Decimal("80.00"))
        self.assertEqual(rateio.valor_original, Decimal("90.20"))
        self.assertEqual(rateio.valor_final, Decimal("80.00"))

    def test_aprovacao_preserva_limite_automatico_quando_post_nao_traz_campo(self):
        self.criar_politica_refeicao_capital("80.00")
        self.despesa.valor = Decimal("90.20")
        self.despesa.valor_aprovado = Decimal("80.00")
        self.despesa.save(update_fields=["valor", "valor_aprovado"])
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)
        self.preparar_relatorio_para_aprovacao()

        aprovar_relatorio(self.relatorio.pk, self.usuario_financeiro(), post_data={})
        self.despesa.refresh_from_db()

        self.assertEqual(self.despesa.valor_aprovado, Decimal("80.00"))
        self.assertEqual(self.despesa.valor_final, Decimal("80.00"))

    def test_aprovacao_nao_grava_valor_solicitado_quando_aprovacao_integral(self):
        self.criar_politica_refeicao_capital("80.00")
        self.despesa.valor = Decimal("70.00")
        self.despesa.valor_aprovado = None
        self.despesa.save(update_fields=["valor", "valor_aprovado"])
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)
        self.preparar_relatorio_para_aprovacao()

        aprovar_relatorio(self.relatorio.pk, self.usuario_financeiro(), post_data={})
        self.despesa.refresh_from_db()

        self.assertIsNone(self.despesa.valor_aprovado)
        self.assertEqual(self.despesa.valor_final, Decimal("70.00"))

    def test_aprovacao_hospedagem_aplica_tecnicos_e_diarias(self):
        self.criar_politica_hospedagem_sao_paulo("900.00")
        self.relatorio.cidade_atendimento = "Sao Paulo"
        self.relatorio.tipo_localidade = "capital"
        self.relatorio.save(update_fields=["cidade_atendimento", "tipo_localidade"])
        self.despesa.tipo = TipoDespesa.HOSPEDAGEM
        self.despesa.descricao = "Hotel Sao Paulo"
        self.despesa.valor = Decimal("2013.91")
        self.despesa.valor_aprovado = None
        self.despesa.data_inicio_hospedagem = date(2026, 8, 1)
        self.despesa.data_fim_hospedagem = date(2026, 8, 2)
        self.despesa.save(
            update_fields=[
                "tipo",
                "descricao",
                "valor",
                "valor_aprovado",
                "data_inicio_hospedagem",
                "data_fim_hospedagem",
            ]
        )
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)
        self.preparar_relatorio_para_aprovacao()

        aprovar_relatorio(self.relatorio.pk, self.usuario_financeiro(), post_data={})
        self.despesa.refresh_from_db()

        self.assertEqual(self.despesa.quantidade_diarias_hospedagem, 1)
        self.assertEqual(self.despesa.valor_politica, Decimal("900.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("900.00"))
        self.assertEqual(self.despesa.valor_final, Decimal("900.00"))

    def test_hospedagem_sem_periodo_valido_nao_multiplica_por_zero(self):
        self.criar_politica_hospedagem_sao_paulo("900.00")
        self.relatorio.cidade_atendimento = "Sao Paulo"
        self.relatorio.save(update_fields=["cidade_atendimento"])
        self.despesa.tipo = TipoDespesa.HOSPEDAGEM
        self.despesa.descricao = "Hotel Sao Paulo"
        self.despesa.valor = Decimal("1010.90")
        self.despesa.data_inicio_hospedagem = None
        self.despesa.data_fim_hospedagem = None
        self.despesa.save(
            update_fields=[
                "tipo",
                "descricao",
                "valor",
                "data_inicio_hospedagem",
                "data_fim_hospedagem",
            ]
        )
        sync_tecnicos_despesa(self.despesa, [self.tecnico_a.pk], self.usuario)

        aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertEqual(self.despesa.quantidade_diarias_hospedagem, 0)
        self.assertEqual(self.despesa.valor_politica, Decimal("900.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("900.00"))
        self.assertEqual(self.despesa.valor_final, Decimal("900.00"))

    def test_aprovado_inicial_preserva_edicao_manual_do_financeiro(self):
        self.criar_politica_refeicao_capital()
        self.despesa.valor = Decimal("200.00")
        self.despesa.valor_aprovado = Decimal("170.00")
        self.despesa.save(update_fields=["valor", "valor_aprovado"])
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )

        alterou = aplicar_politica_valor_aprovado_inicial(self.despesa)
        self.despesa.refresh_from_db()

        self.assertFalse(alterou)
        self.assertEqual(self.despesa.valor_politica, Decimal("160.00"))
        self.assertEqual(self.despesa.valor_aprovado, Decimal("170.00"))

    def test_sync_bloqueia_tecnico_fora_do_relatorio(self):
        erros = sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_c.pk],
            self.usuario,
        )

        self.assertEqual(
            erros,
            ["Selecione apenas técnicos vinculados ao relatório para esta despesa."],
        )
        self.assertFalse(DespesaTecnico.objects.filter(despesa=self.despesa).exists())

    def test_remove_participacao_quando_tecnico_sai_do_relatorio(self):
        sync_tecnicos_despesa(
            self.despesa,
            [self.tecnico_a.pk, self.tecnico_b.pk],
            self.usuario,
        )
        self.relatorio.equipe.filter(tecnico=self.tecnico_b).delete()

        remover_tecnicos_despesas_fora_relatorio(self.relatorio, self.usuario)

        self.assertEqual(
            set(self.despesa.tecnicos_vinculados.values_list("tecnico_id", flat=True)),
            {self.tecnico_a.pk},
        )


class ClienteListViewTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_superuser(
            username="admin-clientes",
            password="senha-teste",
            email="admin@example.com",
        )
        self.client.force_login(self.usuario)

    def test_busca_parcial_retorna_todos_os_clientes_sem_duplicar(self):
        cliente_gestao = Cliente.objects.create(
            nome="ControlSul Gestao Empresarial",
            razao_social="ControlSul Gestao Empresarial Ltda",
            nome_fantasia="ControlSul Gestao",
            cnpj_cpf="11111111000111",
        )
        cliente_transportes = Cliente.objects.create(
            nome="ControlSul Transportes",
            razao_social="ControlSul Transportes Ltda",
            nome_fantasia="ControlSul Transportes",
            cnpj_cpf="22222222000122",
        )
        Cliente.objects.create(
            nome="Outro Cliente",
            cnpj_cpf="33333333000133",
        )

        response = self.client.get(
            reverse("relatorios:cliente_list"),
            {"busca": "con"},
        )

        self.assertEqual(response.status_code, 200)
        ids = [cliente.pk for cliente in response.context["clientes"]]
        self.assertCountEqual(ids, [cliente_gestao.pk, cliente_transportes.pk])
        self.assertEqual(len(ids), len(set(ids)))

    def test_listagem_filtrada_exibe_atalho_para_visualizar_todos(self):
        Cliente.objects.create(nome="Cliente Pendente", valor_km=None)

        response = self.client.get(
            reverse("relatorios:cliente_list"),
            {"valor_km": "pendente"},
        )

        self.assertContains(response, "Visualizar todos")
        self.assertContains(response, reverse("relatorios:cliente_list"))


class ClienteEmpresaGrupoTests(TestCase):
    def test_resolve_empresa_por_correspondencia_exata_sem_confundir_com_outro_cliente(self):
        empresa = Cliente.objects.create(
            nome="CONTROLSUL",
            razao_social="CONTROLSUL GESTAO EMPRESARIAL LTDA",
            ativo=True,
        )
        Cliente.objects.create(
            nome="CONTROLSUL TRANSPORTES",
            razao_social="CONTROLSUL TRANSPORTES LTDA",
            ativo=True,
        )

        encontrado = resolver_cliente_empresa_grupo(EmpresaGrupo.CONTROLSUL)

        self.assertEqual(encontrado, empresa)

    def test_nao_resolve_empresa_quando_correspondencia_e_ambigua(self):
        Cliente.objects.create(nome="CONTROLSUL GESTAO", ativo=True)
        Cliente.objects.create(nome="CONTROLSUL TRANSPORTES", ativo=True)

        encontrado = resolver_cliente_empresa_grupo(EmpresaGrupo.CONTROLSUL)

        self.assertIsNone(encontrado)


class RelatorioTecnicoFlowTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome="Cliente Teste",
            cidade="Curitiba",
            uf="PR",
            valor_km=Decimal("2.50"),
        )
        self.tecnico = Tecnico.objects.create(
            nome="Tecnico Teste",
            email="tecnico@example.com",
        )
        self.usuario_financeiro = get_user_model().objects.create_user(
            username="financeiro",
            password="senha-teste",
        )
        self.grupo_financeiro = Group.objects.get(name="Financeiro")
        self.grupo_tecnico = Group.objects.get(name="Tecnico")
        self.usuario_financeiro.groups.add(self.grupo_financeiro)
        self.client.force_login(self.usuario_financeiro)

    def dados_relatorio(self, **extra):
        dados = {
            "numero": "RT-2026-001",
            "cliente": str(self.cliente.pk),
            "tecnico_responsavel": str(self.tecnico.pk),
            "cidade_atendimento": "Curitiba",
            "uf_atendimento": "PR",
            "tipo_localidade": "interior",
            "data_inicio": "2026-05-01",
            "data_fim": "2026-05-03",
            "motivo": "Atendimento tecnico",
            "centro_custo": "Manutencao",
            "valor_adiantamento": "100.00",
            "observacoes": "",
        }
        dados.update(extra)
        return dados

    def dados_formsets_vazios(self):
        return {
            "cidades-TOTAL_FORMS": "0",
            "cidades-INITIAL_FORMS": "0",
            "cidades-MIN_NUM_FORMS": "0",
            "cidades-MAX_NUM_FORMS": "1000",
            "despesas-TOTAL_FORMS": "0",
            "despesas-INITIAL_FORMS": "0",
            "despesas-MIN_NUM_FORMS": "0",
            "despesas-MAX_NUM_FORMS": "1000",
            "trechos-TOTAL_FORMS": "0",
            "trechos-INITIAL_FORMS": "0",
            "trechos-MIN_NUM_FORMS": "0",
            "trechos-MAX_NUM_FORMS": "1000",
        }

    def dados_relatorio_com_despesa(self, **extra):
        dados = self.dados_relatorio(
            acao="rascunho",
            tipo_relatorio="operacional",
            tipo_reembolso="reembolsavel",
            clientes_relatorio=str(self.cliente.pk),
            tecnico_reembolso=str(self.tecnico.pk),
            tecnicos_equipe=[],
        )
        dados.update(self.dados_formsets_vazios())
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-tipo_documento_comprovante": "recibo",
                "despesas-0-numero_documento_comprovante": "R-001",
                "despesas-0-observacoes": "",
                "despesas-0-clientes": str(self.cliente.pk),
            }
        )
        dados.update(extra)
        return dados

    def criar_relatorio(self, numero):
        return RelatorioTecnico.objects.create(
            numero=numero,
            cliente=self.cliente,
            tecnico_responsavel=self.tecnico,
            cidade_atendimento="Curitiba",
            uf_atendimento="PR",
            tipo_localidade="interior",
            data_inicio="2026-05-01",
            data_fim="2026-05-03",
            motivo="Atendimento tecnico",
            centro_custo="Manutencao",
            valor_adiantamento=Decimal("100.00"),
        )

    def test_status_choices_atuais_incluem_fluxo_operacional(self):
        choices = dict(RelatorioTecnico._meta.get_field("status").choices)

        self.assertIn(StatusRelatorio.CONFERENCIA, choices)
        self.assertIn(StatusRelatorio.AJUSTE, choices)
        self.assertIn(StatusRelatorio.APROVADO, choices)
        self.assertIn(StatusRelatorio.REJEITADO, choices)
        self.assertNotIn("pendente", choices)
        self.assertNotIn("faturado", choices)
        self.assertNotIn("fechado", choices)
        self.assertNotIn("enviado", choices)

    def test_nao_reembolsavel_ignora_cliente_postado_e_vincula_empresa_grupo(self):
        self.usuario_financeiro.first_name = "Usuario"
        self.usuario_financeiro.last_name = "Financeiro"
        self.usuario_financeiro.email = "financeiro@example.com"
        self.usuario_financeiro.save(update_fields=["first_name", "last_name", "email"])
        PerfilUsuario.objects.update_or_create(
            usuario=self.usuario_financeiro,
            defaults={"cadastro_confirmado_em": timezone.now()},
        )
        controlsul = Cliente.objects.create(
            nome="CONTROLSUL",
            razao_social="CONTROLSUL GESTAO EMPRESARIAL LTDA",
            cidade="Curitiba",
            uf="PR",
            valor_km=Decimal("1.35"),
        )
        dados = self.dados_relatorio(
            acao="rascunho",
            tipo_relatorio="operacional",
            tipo_reembolso="nao_reembolsavel",
            empresa_grupo=EmpresaGrupo.CONTROLSUL,
            clientes_relatorio=str(self.cliente.pk),
            tecnico_reembolso=str(self.tecnico.pk),
            tecnicos_equipe=[],
        )
        dados.update(self.dados_formsets_vazios())
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-clientes": "",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.order_by("-pk").first()
        self.assertIsNotNone(
            relatorio,
            msg=(
                f"status={response.status_code}; location={response.get('Location')}; "
                f"form={response.context['form'].errors if response.context else None}; "
                f"resumo={response.context.get('resumo_erros') if response.context else None}"
            ),
        )
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.cliente_id, controlsul.pk)
        self.assertEqual(
            list(relatorio.clientes_vinculados.values_list("cliente_id", flat=True)),
            [controlsul.pk],
        )

    def test_autosave_rascunho_substitui_versao_anterior(self):
        relatorio = self.criar_relatorio("RT-2026-AUTOSAVE")
        relatorio.criado_por = self.usuario_financeiro
        relatorio.status = StatusRelatorio.RASCUNHO
        relatorio.save(update_fields=["criado_por", "status"])
        url = reverse("relatorios:relatorio_autosave")

        payload = {
            "autosave_key": "autosave-teste",
            "relatorio_id": str(relatorio.pk),
            "motivo": "Primeira versao",
            "despesas-TOTAL_FORMS": "1",
            "trechos-TOTAL_FORMS": "0",
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        payload["motivo"] = "Segunda versao"
        payload["despesas-TOTAL_FORMS"] = "2"
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)

        autosaves = RelatorioAutoSave.objects.filter(
            usuario=self.usuario_financeiro,
            chave="autosave-teste",
        )
        self.assertEqual(autosaves.count(), 1)
        autosave = autosaves.get()
        self.assertEqual(autosave.relatorio, relatorio)
        self.assertEqual(autosave.payload["motivo"], "Segunda versao")
        self.assertEqual(autosave.despesas_count, 2)

    def test_autosave_bloqueia_relatorio_fora_de_rascunho(self):
        relatorio = self.criar_relatorio("RT-2026-AUTOSAVE-BLOQUEADO")
        relatorio.criado_por = self.usuario_financeiro
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["criado_por", "status"])

        response = self.client.post(
            reverse("relatorios:relatorio_autosave"),
            {
                "autosave_key": "autosave-bloqueado",
                "relatorio_id": str(relatorio.pk),
                "motivo": "Nao deve salvar",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            RelatorioAutoSave.objects.filter(chave="autosave-bloqueado").exists()
        )

    def test_submit_assincrono_invalido_retorna_422_sem_criar_relatorio_ou_anexo(self):
        arquivo_a = SimpleUploadedFile(
            "nota_a.pdf",
            b"arquivo-a",
            content_type="application/pdf",
        )
        arquivo_b = SimpleUploadedFile(
            "nota_b.pdf",
            b"arquivo-b",
            content_type="application/pdf",
        )
        dados = self.dados_relatorio_com_despesa(
            **{
                "despesas-0-descricao": "",
                "upload_expected_manifest": json.dumps(
                    [
                        {
                            "campo": "despesas-0-comprovante",
                            "nome": "nota_a.pdf",
                            "tamanho": len(b"arquivo-a"),
                            "mime": "application/pdf",
                        },
                        {
                            "campo": "despesas-0-comprovante",
                            "nome": "nota_b.pdf",
                            "tamanho": len(b"arquivo-b"),
                            "mime": "application/pdf",
                        },
                    ]
                ),
                "despesas-0-comprovante": [arquivo_a, arquivo_b],
            }
        )

        response = self.client.post(
            reverse("relatorios:relatorio_create"),
            dados,
            HTTP_X_RELATORIO_ASYNC_SUBMIT="1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["validation_error"])
        self.assertTrue(
            any(
                erro.get("name") == "despesas-0-descricao"
                for erro in payload["field_errors"]
            )
        )
        self.assertFalse(RelatorioTecnico.objects.filter(motivo="Atendimento tecnico").exists())
        self.assertFalse(AnexoRelatorio.objects.exists())

    def test_submit_normal_invalido_mantem_render_html_existente(self):
        dados = self.dados_relatorio_com_despesa(**{"despesas-0-descricao": ""})

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corrija os erros indicados antes de salvar.")
        self.assertFalse(RelatorioTecnico.objects.filter(motivo="Atendimento tecnico").exists())

    def test_submit_assincrono_valido_preserva_redirect_de_sucesso(self):
        dados = self.dados_relatorio_com_despesa(
            motivo="Relatorio async valido",
        )

        response = self.client.post(
            reverse("relatorios:relatorio_create"),
            dados,
            HTTP_X_RELATORIO_ASYNC_SUBMIT="1",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        relatorio = RelatorioTecnico.objects.get(motivo="Relatorio async valido")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )

    def test_trecho_calcula_valor_total_no_save(self):
        relatorio = self.criar_relatorio("RT-2026-002")

        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
        )

        self.assertEqual(trecho.valor_calculado, Decimal("250.00"))
        self.assertFalse(trecho.km_fora_politica)

    def test_cria_relatorio_com_despesa_e_trecho_em_conferencia(self):
        dados = self.dados_relatorio(acao="enviar")
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
                "trechos-TOTAL_FORMS": "1",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
                "trechos-0-id": "",
                "trechos-0-ordem": "0",
                "trechos-0-data": "2026-05-02",
                "trechos-0-origem": "Curitiba",
                "trechos-0-destino": "Ponta Grossa",
                "trechos-0-km": "100.0",
                "trechos-0-valor_km": "2.50",
                "trechos-0-observacao": "",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(motivo="Atendimento tecnico")
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertEqual(relatorio.despesas.count(), 1)
        self.assertEqual(relatorio.trechos.count(), 1)
        self.assertEqual(relatorio.total_despesas, Decimal("300.00"))
        self.assertEqual(relatorio.numero, "1")
        self.assertTrue(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                acao="Relatório criado",
            ).exists()
        )
        self.assertTrue(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                acao="Relatório enviado para conferência",
            ).exists()
        )

    def test_salva_datas_em_formato_pt_br_no_formulario(self):
        dados = self.dados_relatorio(
            acao="enviar",
            data_inicio="01/05/2026",
            data_fim="03/05/2026",
            motivo="Atendimento com datas pt-br",
        )
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "02/05/2026",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
                "trechos-TOTAL_FORMS": "1",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
                "trechos-0-id": "",
                "trechos-0-ordem": "0",
                "trechos-0-data": "02/05/2026",
                "trechos-0-origem": "Curitiba",
                "trechos-0-destino": "Ponta Grossa",
                "trechos-0-km": "100.0",
                "trechos-0-valor_km": "2.50",
                "trechos-0-observacao": "",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(motivo="Atendimento com datas pt-br")
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.data_inicio.isoformat(), "2026-05-01")
        self.assertEqual(relatorio.data_fim.isoformat(), "2026-05-03")
        self.assertEqual(relatorio.despesas.get().data.isoformat(), "2026-05-02")
        self.assertEqual(relatorio.trechos.get().data.isoformat(), "2026-05-02")

    def test_numero_manual_do_post_e_ignorado_no_cadastro(self):
        self.criar_relatorio("10")
        dados = self.dados_relatorio(
            numero="999",
            acao="rascunho",
            motivo="Cadastro com numero automatico",
        )
        dados.update(self.dados_formsets_vazios())

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(
            motivo="Cadastro com numero automatico"
        )
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertIsNone(relatorio.numero)
        self.assertEqual(relatorio.identificador, f"Rascunho #{relatorio.pk}")

    def test_envio_para_conferencia_gera_numero_oficial_no_momento_do_envio(self):
        self.criar_relatorio("10")
        dados = self.dados_relatorio(
            numero="999",
            acao="enviar",
            motivo="Envio com numero oficial tardio",
        )
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
                "trechos-TOTAL_FORMS": "0",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(
            motivo="Envio com numero oficial tardio"
        )
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertEqual(relatorio.numero, "11")

    def test_duplica_relatorio_com_linhas_sem_datas_e_sem_dados_financeiros(self):
        apoio = Tecnico.objects.create(
            nome="Tecnico Apoio",
            email="apoio@example.com",
        )
        relatorio = self.criar_relatorio("RT-2026-011")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_por = self.usuario_financeiro
        relatorio.save(update_fields=["status", "aprovado_por"])
        RelatorioTecnicoEquipe.objects.create(relatorio=relatorio, tecnico=apoio)
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            valor_aprovado=Decimal("45.00"),
            quem_pagou="tecnico",
            comprovante="comprovantes/original.pdf",
            observacoes="Observacao da despesa",
        )
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
            valor_km_aprovado=Decimal("2.00"),
            observacao="Observacao do trecho",
        )

        response = self.client.post(
            reverse("relatorios:relatorio_duplicate", kwargs={"pk": relatorio.pk})
        )

        novo = RelatorioTecnico.objects.exclude(pk=relatorio.pk).get()
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_update", kwargs={"pk": novo.pk}),
        )
        self.assertNotEqual(novo.pk, relatorio.pk)
        self.assertNotEqual(novo.numero, relatorio.numero)
        self.assertEqual(novo.status, StatusRelatorio.RASCUNHO)
        self.assertIsNone(novo.aprovado_por)
        self.assertIsNone(novo.aprovado_em)
        self.assertEqual(novo.valor_adiantamento, relatorio.valor_adiantamento)
        self.assertEqual(novo.cliente, relatorio.cliente)
        self.assertEqual(novo.tecnico_responsavel, relatorio.tecnico_responsavel)
        self.assertEqual(novo.observacoes, relatorio.observacoes)
        self.assertTrue(novo.equipe.filter(tecnico=apoio).exists())

        nova_despesa = novo.despesas.get()
        self.assertIsNone(nova_despesa.data)
        self.assertEqual(nova_despesa.tipo, despesa.tipo)
        self.assertEqual(nova_despesa.descricao, despesa.descricao)
        self.assertEqual(nova_despesa.valor, despesa.valor)
        self.assertIsNone(nova_despesa.valor_aprovado)
        self.assertFalse(nova_despesa.comprovante)

        novo_trecho = novo.trechos.get()
        self.assertIsNone(novo_trecho.data)
        self.assertEqual(novo_trecho.origem, trecho.origem)
        self.assertEqual(novo_trecho.destino, trecho.destino)
        self.assertEqual(novo_trecho.km, trecho.km)
        self.assertEqual(novo_trecho.valor_km, trecho.valor_km)
        self.assertIsNone(novo_trecho.valor_km_aprovado)

        relatorio.refresh_from_db()
        despesa.refresh_from_db()
        trecho.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertEqual(despesa.valor_aprovado, Decimal("45.00"))
        self.assertEqual(trecho.valor_km_aprovado, Decimal("2.00"))

        detalhe = self.client.get(
            reverse("relatorios:relatorio_detail", kwargs={"pk": novo.pk})
        )
        self.assertContains(detalhe, "A preencher")

    def test_endpoints_importacao_expoem_apenas_dados_operacionais(self):
        relatorio = self.criar_relatorio("RT-2026-013")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_por = self.usuario_financeiro
        relatorio.save(update_fields=["status", "aprovado_por"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            valor_aprovado=Decimal("45.00"),
            quem_pagou="tecnico",
            comprovante="comprovantes/original.pdf",
            observacoes="Observacao da despesa",
        )
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
            valor_km_aprovado=Decimal("2.00"),
            observacao="Observacao financeira",
        )

        lista = self.client.get(
            reverse("relatorios:relatorio_import_list"),
            {"busca": relatorio.numero},
        )
        self.assertEqual(lista.status_code, 200)
        self.assertEqual(lista.json()["relatorios"][0]["id"], relatorio.pk)

        detalhe = self.client.get(
            reverse("relatorios:relatorio_import_detail", kwargs={"pk": relatorio.pk})
        )
        self.assertEqual(detalhe.status_code, 200)
        payload = detalhe.json()
        self.assertEqual(payload["cliente_id"], self.cliente.pk)
        self.assertEqual(payload["tecnico_id"], self.tecnico.pk)
        self.assertEqual(payload["valor_adiantamento"], "100.00")
        self.assertEqual(payload["despesas"][0]["tipo"], despesa.tipo)
        self.assertEqual(payload["despesas"][0]["descricao"], despesa.descricao)
        self.assertEqual(payload["despesas"][0]["valor"], "50.00")
        self.assertEqual(payload["despesas"][0]["observacoes"], despesa.observacoes)
        self.assertEqual(payload["trechos"][0]["origem"], trecho.origem)
        self.assertEqual(payload["trechos"][0]["destino"], trecho.destino)
        self.assertEqual(payload["trechos"][0]["km"], "100.0")
        self.assertEqual(payload["trechos"][0]["valor_km"], "2.5000")
        self.assertNotIn("aprovado_por", payload)
        self.assertNotIn("aprovado_em", payload)
        self.assertNotIn("valor_aprovado", payload["despesas"][0])
        self.assertNotIn("comprovante", payload["despesas"][0])
        self.assertNotIn("valor_km_aprovado", payload["trechos"][0])
        self.assertNotIn("observacao", payload["trechos"][0])

    def test_linha_vazia_adicionada_nao_cria_despesa_em_rascunho(self):
        dados = self.dados_relatorio(numero="RT-2026-003", acao="rascunho")
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "",
                "despesas-0-tipo": "",
                "despesas-0-descricao": "",
                "despesas-0-valor": "",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
            }
        )
        dados.update(
            {
                "trechos-TOTAL_FORMS": "0",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(motivo="Atendimento tecnico")
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.status, StatusRelatorio.RASCUNHO)
        self.assertEqual(relatorio.despesas.count(), 0)

    def test_delete_do_formset_remove_despesa_existente(self):
        relatorio = self.criar_relatorio("RT-2026-004")
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )

        dados = self.dados_relatorio(numero="RT-2026-004", acao="rascunho")
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "1",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": str(despesa.pk),
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "Almoco",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
                "despesas-0-DELETE": "on",
            }
        )
        dados.update(
            {
                "trechos-TOTAL_FORMS": "0",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
            }
        )

        response = self.client.post(
            reverse("relatorios:relatorio_update", kwargs={"pk": relatorio.pk}),
            dados,
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertFalse(ItemDespesa.objects.filter(pk=despesa.pk).exists())

    def test_edicao_renderiza_valores_existentes_dos_formsets(self):
        relatorio = self.criar_relatorio("RT-2026-008")
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
        )

        response = self.client.get(
            reverse("relatorios:relatorio_update", kwargs={"pk": relatorio.pk})
        )

        self.assertContains(response, 'name="despesas-0-data"')
        self.assertContains(response, 'value="2026-05-02"')
        self.assertContains(response, 'value="Almoco"')
        self.assertContains(response, 'value="50.00"')
        self.assertContains(response, 'name="trechos-0-data"')
        self.assertContains(response, 'value="Curitiba"')
        self.assertContains(response, 'value="100.0"')

    def test_formset_com_indice_fantasma_salva_linha_valida(self):
        dados = self.dados_relatorio(numero="RT-2026-009", acao="rascunho")
        dados.update(
            {
                "despesas-TOTAL_FORMS": "2",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-DELETE": "on",
                "despesas-1-id": "",
                "despesas-1-ordem": "1",
                "despesas-1-data": "2026-05-02",
                "despesas-1-tipo": "alimentacao",
                "despesas-1-descricao": "Almoco",
                "despesas-1-valor": "50.00",
                "despesas-1-quem_pagou": "tecnico",
                "despesas-1-observacoes": "",
                "trechos-TOTAL_FORMS": "0",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        relatorio = RelatorioTecnico.objects.get(motivo="Atendimento tecnico")
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.despesas.count(), 1)

    def test_descricao_despesa_exibe_erro_inline(self):
        dados = self.dados_relatorio(numero="RT-2026-010", acao="rascunho")
        dados.update(
            {
                "despesas-TOTAL_FORMS": "1",
                "despesas-INITIAL_FORMS": "0",
                "despesas-MIN_NUM_FORMS": "0",
                "despesas-MAX_NUM_FORMS": "1000",
                "despesas-0-id": "",
                "despesas-0-ordem": "0",
                "despesas-0-data": "2026-05-02",
                "despesas-0-tipo": "alimentacao",
                "despesas-0-descricao": "",
                "despesas-0-valor": "50.00",
                "despesas-0-quem_pagou": "tecnico",
                "despesas-0-observacoes": "",
                "trechos-TOTAL_FORMS": "0",
                "trechos-INITIAL_FORMS": "0",
                "trechos-MIN_NUM_FORMS": "0",
                "trechos-MAX_NUM_FORMS": "1000",
            }
        )

        response = self.client.post(reverse("relatorios:relatorio_create"), dados)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe a descrição.")
        self.assertContains(response, 'id="id_despesas-0-descricao"')
        self.assertContains(response, "is-invalid")

    def test_aprovacao_salva_valores_aprovados_inline(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-005")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.APROVADO},
            ),
            {
                f"despesa_{despesa.pk}_valor_aprovado": "45.50",
                f"trecho_{trecho.pk}_valor_km_aprovado": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        despesa.refresh_from_db()
        trecho.refresh_from_db()
        relatorio.refresh_from_db()
        self.assertEqual(despesa.valor_aprovado, Decimal("45.50"))
        self.assertEqual(trecho.valor_km_aprovado, Decimal("2.50"))
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertIsNotNone(relatorio.aprovado_em)
        self.assertEqual(relatorio.aprovado_por, self.usuario_financeiro)
        self.assertTrue(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                usuario=self.usuario_financeiro,
                acao="Relatório aprovado",
            ).exists()
        )
        self.assertTrue(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                usuario=self.usuario_financeiro,
                acao="Valor aprovado alterado",
            ).exists()
        )
        adiantamento = Adiantamento.objects.get(relatorio=relatorio)
        self.assertEqual(adiantamento.tipo, TipoAdiantamento.ADIANTAMENTO)
        self.assertEqual(adiantamento.valor, Decimal("100.00"))
        self.assertEqual(adiantamento.tecnico, self.tecnico)
        self.assertIn(relatorio.numero, adiantamento.descricao)

    def test_usuario_autorizado_reabre_relatorio_aprovado_recalculando_financeiro_vivo(self):
        usuario_autorizado = get_user_model().objects.create_user(
            username=r"control.local\gabriel.oliveira",
            password="senha-teste",
        )
        usuario_autorizado.groups.add(self.grupo_financeiro)
        PoliticaValor.objects.create(
            chave="REFEICAO_INTERIOR",
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="interior",
            descricao="Refeicao Interior",
            limite_valor=Decimal("80.00"),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )
        relatorio = self.criar_relatorio("RT-2026-REABRIR")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_em = timezone.now()
        relatorio.aprovado_por = self.usuario_financeiro
        relatorio.tecnico_reembolso = self.tecnico
        relatorio.save(
            update_fields=[
                "status",
                "aprovado_em",
                "aprovado_por",
                "tecnico_reembolso",
            ]
        )
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco aprovado",
            valor=Decimal("90.20"),
            valor_aprovado=Decimal("90.20"),
            quem_pagou="tecnico",
        )
        snapshot = criar_snapshot_financeiro(relatorio, self.usuario_financeiro)
        snapshot_pk = snapshot.pk
        checksum = snapshot.checksum
        aprovado_em = relatorio.aprovado_em
        aprovado_por_id = relatorio.aprovado_por_id
        totais_antes = {
            "total_solicitado": relatorio.total_solicitado,
            "despesas": relatorio.despesas.count(),
            "trechos": relatorio.trechos.count(),
        }

        self.client.force_login(usuario_autorizado)
        response = self.client.post(
            reverse("relatorios:relatorio_reabrir", kwargs={"pk": relatorio.pk})
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        despesa.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertEqual(relatorio.aprovado_em, aprovado_em)
        self.assertEqual(relatorio.aprovado_por_id, aprovado_por_id)
        self.assertEqual(snapshot.checksum, checksum)
        self.assertEqual(relatorio.total_solicitado, totais_antes["total_solicitado"])
        self.assertEqual(relatorio.despesas.count(), totais_antes["despesas"])
        self.assertEqual(relatorio.trechos.count(), totais_antes["trechos"])
        self.assertEqual(despesa.valor_politica, Decimal("80.00"))
        self.assertEqual(despesa.valor_aprovado, Decimal("80.00"))
        self.assertEqual(despesa.valor_final, Decimal("80.00"))
        self.assertEqual(relatorio.total_aprovado, Decimal("80.00"))
        rateio = despesa.rateios.get(cliente=self.cliente)
        self.assertEqual(rateio.valor_original, Decimal("90.20"))
        self.assertEqual(rateio.valor_final, Decimal("80.00"))
        self.assertTrue(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                usuario=usuario_autorizado,
                tipo_evento=TipoEventoHistorico.REABERTO,
            ).exists()
        )

        aprovar_relatorio(relatorio.pk, self.usuario_financeiro, post_data={})
        relatorio.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertEqual(snapshot.pk, snapshot_pk)
        self.assertNotEqual(snapshot.checksum, checksum)
        self.assertEqual(snapshot.total_aprovado, Decimal("80.00"))
        self.assertEqual(snapshot.payload["despesas"][0]["valor_final"], "80.00")

    def test_usuario_nao_autorizado_nao_reabre_relatorio_aprovado(self):
        relatorio = self.criar_relatorio("RT-2026-REABRIR-NEGADO")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_em = timezone.now()
        relatorio.aprovado_por = self.usuario_financeiro
        relatorio.save(update_fields=["status", "aprovado_em", "aprovado_por"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa aprovada",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )
        snapshot = criar_snapshot_financeiro(relatorio, self.usuario_financeiro)
        checksum = snapshot.checksum

        self.client.force_login(self.usuario_financeiro)
        response = self.client.post(
            reverse("relatorios:relatorio_reabrir", kwargs={"pk": relatorio.pk})
        )

        self.assertEqual(response.status_code, 403)
        relatorio.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertEqual(snapshot.checksum, checksum)
        self.assertFalse(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                tipo_evento=TipoEventoHistorico.REABERTO,
            ).exists()
        )

    def test_botao_reabrir_aparece_apenas_para_usuario_autorizado(self):
        usuario_autorizado = get_user_model().objects.create_user(
            username=r"control.local\gabriel.oliveira",
            password="senha-teste",
        )
        usuario_autorizado.groups.add(self.grupo_financeiro)
        relatorio = self.criar_relatorio("RT-2026-REABRIR-BOTAO")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.aprovado_em = timezone.now()
        relatorio.aprovado_por = self.usuario_financeiro
        relatorio.save(update_fields=["status", "aprovado_em", "aprovado_por"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa aprovada",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )
        criar_snapshot_financeiro(relatorio, self.usuario_financeiro)
        url = reverse("relatorios:relatorio_consulta", kwargs={"pk": relatorio.pk})

        self.client.force_login(self.usuario_financeiro)
        response = self.client.get(url)
        self.assertNotContains(response, "Reabrir relatório")

        self.client.force_login(usuario_autorizado)
        response = self.client.get(url)
        self.assertContains(response, "Reabrir relatório")

    def test_usuario_comum_sem_grupo_financeiro_nao_aprova_relatorio(self):
        usuario_comum = get_user_model().objects.create_user(
            username="tecnico",
            password="senha-teste",
        )
        self.client.force_login(usuario_comum)
        relatorio = self.criar_relatorio("RT-2026-006")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.APROVADO},
            ),
            {f"despesa_{despesa.pk}_valor_aprovado": "10.00"},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        despesa.refresh_from_db()
        relatorio.refresh_from_db()
        self.assertIsNone(despesa.valor_aprovado)
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertIsNone(relatorio.aprovado_por)

    def test_usuario_anonimo_e_redirecionado_ao_tentar_aprovar(self):
        self.client.logout()
        relatorio = self.criar_relatorio("RT-2026-012")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.APROVADO},
            ),
            {f"despesa_{despesa.pk}_valor_aprovado": "10.00"},
        )

        self.assertRedirects(
            response,
            f"/login/?next=/relatorios/{relatorio.pk}/status/aprovado/",
        )
        despesa.refresh_from_db()
        relatorio.refresh_from_db()
        self.assertIsNone(despesa.valor_aprovado)
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertIsNone(relatorio.aprovado_por)

    def test_aprovacao_bloqueia_total_aprovado_zerado(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-030")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa rejeitada",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
            rejeitado=True,
            status_financeiro=StatusFinanceiroItem.REJEITADO,
            motivo_rejeicao="Fora da politica",
            motivo_recusa="Fora da politica",
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.APROVADO},
            )
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertFalse(
            HistoricoRelatorio.objects.filter(
                relatorio=relatorio,
                tipo_evento=TipoEventoHistorico.APROVADO,
            ).exists()
        )

    def test_transicao_invalida_nao_aprova_rascunho(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-031")
        relatorio.status = StatusRelatorio.RASCUNHO
        relatorio.save(update_fields=["status"])

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.APROVADO},
            )
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.RASCUNHO)

    def test_solicitar_ajuste_exige_justificativa_e_mantem_status(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-016")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.AJUSTE},
            ),
            {"motivo_rejeicao": "   "},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.CONFERENCIA)
        self.assertEqual(relatorio.motivo_rejeicao, "")

    def test_solicitar_ajuste_salva_justificativa_e_permite_edicao_tecnico(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-017")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.AJUSTE},
            ),
            {"motivo_rejeicao": "Corrigir comprovantes e descrições."},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.AJUSTE)
        self.assertEqual(
            relatorio.motivo_rejeicao, "Corrigir comprovantes e descrições."
        )
        historico = HistoricoRelatorio.objects.get(
            relatorio=relatorio,
            acao="Financeiro solicitou ajustes",
        )
        self.assertEqual(historico.usuario, self.usuario_financeiro)
        self.assertIn("Corrigir comprovantes", historico.descricao)

        usuario_tecnico = get_user_model().objects.create_user(
            username="tecnico-ajuste",
            password="senha-teste",
        )
        self.client.force_login(usuario_tecnico)
        response_get = self.client.get(
            reverse("relatorios:relatorio_update", kwargs={"pk": relatorio.pk})
        )
        self.assertEqual(response_get.status_code, 200)

    def test_rejeitar_relatorio_salva_justificativa_e_bloqueia_edicao(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-018")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])

        response = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.REJEITADO},
            ),
            {"motivo_rejeicao": "Relatório incompatível com o atendimento."},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.REJEITADO)
        self.assertEqual(
            relatorio.motivo_rejeicao,
            "Relatório incompatível com o atendimento.",
        )
        historico = HistoricoRelatorio.objects.get(
            relatorio=relatorio,
            acao="Relatório rejeitado definitivamente",
        )
        self.assertEqual(historico.usuario, self.usuario_financeiro)
        self.assertIn("incompatível", historico.descricao)

        response_get = self.client.get(
            reverse("relatorios:relatorio_update", kwargs={"pk": relatorio.pk})
        )
        self.assertRedirects(
            response_get,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )

    def test_detalhe_exibe_historico_do_relatorio(self):
        relatorio = self.criar_relatorio("RT-2026-019")
        HistoricoRelatorio.objects.create(
            relatorio=relatorio,
            usuario=self.usuario_financeiro,
            acao="Relatório criado",
            descricao="Relatório RT-2026-019 criado.",
        )

        response = self.client.get(
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk})
        )

        self.assertContains(response, "Histórico do relatório")
        self.assertContains(response, "Relatório criado")
        self.assertContains(response, "Relatório RT-2026-019 criado.")

    def test_financeiro_rejeita_e_restaura_despesa_individual(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-020")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_item_financeiro",
                kwargs={
                    "pk": relatorio.pk,
                    "tipo": "despesa",
                    "item_pk": despesa.pk,
                    "acao": "rejeitar",
                },
            ),
            {"motivo_rejeicao": "Comprovante ilegível."},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        despesa.refresh_from_db()
        self.assertEqual(despesa.status_financeiro, StatusFinanceiroItem.REJEITADO)
        self.assertEqual(despesa.motivo_recusa, "Comprovante ilegível.")
        self.assertTrue(despesa.rejeitado)
        self.assertEqual(despesa.motivo_rejeicao, "Comprovante ilegível.")
        self.assertEqual(despesa.rejeitado_por, self.usuario_financeiro)
        self.assertIsNotNone(despesa.rejeitado_em)
        historico_rejeicao = HistoricoRelatorio.objects.get(
            relatorio=relatorio,
            tipo_evento=TipoEventoHistorico.ITEM_REJEITADO,
        )
        self.assertEqual(historico_rejeicao.dados_json["tipo_item"], "despesa")
        self.assertEqual(
            historico_rejeicao.dados_json["motivo"], "Comprovante ilegível."
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_item_financeiro",
                kwargs={
                    "pk": relatorio.pk,
                    "tipo": "despesa",
                    "item_pk": despesa.pk,
                    "acao": "restaurar",
                },
            )
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        despesa.refresh_from_db()
        self.assertEqual(despesa.status_financeiro, StatusFinanceiroItem.APROVADO)
        self.assertEqual(despesa.motivo_recusa, "")
        self.assertFalse(despesa.rejeitado)
        self.assertEqual(despesa.motivo_rejeicao, "")
        self.assertIsNone(despesa.rejeitado_por)
        self.assertIsNone(despesa.rejeitado_em)
        historico_reativacao = HistoricoRelatorio.objects.get(
            relatorio=relatorio,
            tipo_evento=TipoEventoHistorico.ITEM_REATIVADO,
        )
        self.assertEqual(historico_reativacao.dados_json["tipo_item"], "despesa")
        self.assertEqual(
            historico_reativacao.dados_json["item_id"], despesa.pk
        )

    def test_totais_aprovados_consideram_valor_aprovado_e_itens_rejeitados(self):
        relatorio = self.criar_relatorio("RT-2026-023")
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa ajustada",
            valor=Decimal("100.00"),
            valor_aprovado=Decimal("80.00"),
            quem_pagou="tecnico",
        )
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=1,
            data="2026-05-02",
            tipo="pedagio",
            descricao="Despesa rejeitada",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
            status_financeiro=StatusFinanceiroItem.REJEITADO,
            motivo_recusa="Duplicada",
            rejeitado=True,
            motivo_rejeicao="Duplicada",
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
            valor_km_aprovado=Decimal("2.00"),
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=1,
            data="2026-05-02",
            origem="Curitiba",
            destino="Londrina",
            km=Decimal("50.0"),
            valor_km=Decimal("2.50"),
            status_financeiro=StatusFinanceiroItem.REJEITADO,
            motivo_recusa="Duplicado",
            rejeitado=True,
            motivo_rejeicao="Duplicado",
        )

        self.assertEqual(relatorio.total_solicitado, Decimal("352.50"))
        self.assertEqual(relatorio.total_aprovado_despesas, Decimal("80.00"))
        self.assertEqual(relatorio.total_aprovado_km, Decimal("135.00"))
        self.assertEqual(relatorio.total_aprovado, Decimal("215.00"))
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("137.50"))

    def test_resumo_financeiro_km_usa_reembolso_tecnico_e_separa_cobranca_cliente(self):
        self.cliente.valor_km = Decimal("1.85")
        self.cliente.save(update_fields=["valor_km"])
        relatorio = self.criar_relatorio("RT-2026-024")
        relatorio.km_excedente_interno = Decimal("4.00")
        relatorio.save(update_fields=["km_excedente_interno"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa tecnico",
            valor=Decimal("660.65"),
            quem_pagou="tecnico",
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.00"),
            valor_km=Decimal("1.85"),
        )

        self.assertEqual(relatorio.total_km, Decimal("192.40"))
        self.assertEqual(relatorio.total_km_reembolso_tecnico_solicitado, Decimal("140.40"))
        self.assertEqual(relatorio.total_km_reembolso_tecnico, Decimal("140.40"))
        self.assertEqual(relatorio.total_solicitado, Decimal("801.05"))
        self.assertEqual(relatorio.total_aprovado_km, Decimal("140.40"))
        self.assertEqual(relatorio.total_aprovado, Decimal("801.05"))
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("0.00"))
        self.assertEqual(relatorio.total_a_reembolsar, Decimal("801.05"))
        self.assertEqual(relatorio.total_km_excesso_reducao_clientes, Decimal("52.00"))
        resumo_clientes = resumo_financeiro_por_cliente(relatorio)
        self.assertEqual(resumo_clientes["erros"], [])
        self.assertEqual(resumo_clientes["clientes"][0].valor_km_solicitado, Decimal("192.40"))
        self.assertEqual(resumo_clientes["clientes"][0].valor_km_reembolso_tecnico, Decimal("140.40"))
        self.assertEqual(resumo_clientes["clientes"][0].total_solicitado, Decimal("801.05"))
        self.assertEqual(resumo_clientes["clientes"][0].total_aprovado, Decimal("801.05"))

    def test_politica_automatica_reflete_reducao_no_resumo_financeiro(self):
        PoliticaValor.objects.create(
            chave="REFEICAO_CAPITAL",
            tipo_politica=PoliticaValor.TipoPolitica.REFEICAO,
            tipo_despesa=TipoDespesa.ALIMENTACAO,
            tipo_localidade="capital",
            descricao="Refeicao Capital",
            limite_valor=Decimal("80.00"),
            vigencia_inicio=date(2026, 1, 1),
            ativo=True,
        )
        tecnico_extra = Tecnico.objects.create(
            nome="Tecnico Extra",
            email="extra@example.com",
        )
        relatorio = self.criar_relatorio("RT-2026-POLITICA-RESUMO")
        relatorio.tipo_localidade = "capital"
        relatorio.save(update_fields=["tipo_localidade"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo=TipoDespesa.ALIMENTACAO,
            descricao="Almoco equipe",
            valor=Decimal("200.00"),
            quem_pagou="tecnico",
        )
        DespesaTecnico.objects.create(despesa=despesa, tecnico=self.tecnico)
        DespesaTecnico.objects.create(despesa=despesa, tecnico=tecnico_extra)

        aplicar_politica_valor_aprovado_inicial(despesa)
        despesa.refresh_from_db()

        self.assertEqual(despesa.valor_politica, Decimal("160.00"))
        self.assertEqual(despesa.valor_aprovado, Decimal("160.00"))
        self.assertTrue(despesa.politica_aplicada_automaticamente)
        self.assertFalse(despesa.politica_alterada_manualmente)
        self.assertEqual(relatorio.total_despesas_reembolsaveis, Decimal("160.00"))
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("40.00"))
        resumo_clientes = resumo_financeiro_por_cliente(relatorio)
        self.assertEqual(resumo_clientes["clientes"][0].total_solicitado, Decimal("200.00"))
        self.assertEqual(resumo_clientes["clientes"][0].total_aprovado, Decimal("160.00"))
        self.assertEqual(resumo_clientes["clientes"][0].diferenca_removida, Decimal("40.00"))
        self.assertTrue(resumo_clientes["clientes"][0].tem_politica_aplicada)

        despesa.valor_aprovado = Decimal("150.00")
        despesa.save(update_fields=["valor_aprovado"])
        self.assertFalse(despesa.politica_aplicada_automaticamente)
        self.assertTrue(despesa.politica_alterada_manualmente)

    def test_cobranca_cliente_menor_que_reembolso_nao_gera_valor_removido(self):
        self.cliente.valor_km = Decimal("1.00")
        self.cliente.save(update_fields=["valor_km"])
        relatorio = self.criar_relatorio("RT-2026-025")
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.00"),
            valor_km=Decimal("1.00"),
        )

        self.assertEqual(relatorio.valor_km_ressarcir, Decimal("135.00"))
        self.assertEqual(relatorio.valor_km_cobrar_cliente, Decimal("100.00"))
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("0.00"))

    def test_valor_km_nao_e_validado_quando_relatorio_nao_tem_trechos(self):
        self.cliente.valor_km = None
        self.cliente.save(update_fields=["valor_km"])
        relatorio = self.criar_relatorio("RT-2026-SEM-KM")
        RelatorioCliente.objects.create(
            relatorio=relatorio,
            cliente=self.cliente,
            motivo_viagem="Atendimento sem deslocamento",
        )

        self.assertEqual(clientes_relatorio_sem_valor_km(relatorio), [])

    def test_valor_km_valida_so_cliente_usado_e_reflete_atualizacao_imediata(self):
        cliente_sem_deslocamento = self.cliente
        cliente_sem_deslocamento.valor_km = None
        cliente_sem_deslocamento.save(update_fields=["valor_km"])
        cliente_do_trecho = Cliente.objects.create(
            nome="Cliente do Trecho",
            valor_km=None,
        )
        relatorio = self.criar_relatorio("RT-2026-CLIENTE-KM")
        RelatorioCliente.objects.create(
            relatorio=relatorio,
            cliente=cliente_sem_deslocamento,
            ordem=0,
            motivo_viagem="Apenas despesas",
        )
        RelatorioCliente.objects.create(
            relatorio=relatorio,
            cliente=cliente_do_trecho,
            ordem=1,
            motivo_viagem="Deslocamento",
        )
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("10.00"),
            valor_km=Decimal("1.35"),
        )
        TrechoKMCliente.objects.create(
            trecho=trecho,
            cliente=cliente_do_trecho,
        )

        pendentes = clientes_relatorio_sem_valor_km(relatorio)
        self.assertEqual([cliente.pk for cliente in pendentes], [cliente_do_trecho.pk])

        cliente_do_trecho.valor_km = Decimal("1.85")
        cliente_do_trecho.save(update_fields=["valor_km"])

        self.assertEqual(clientes_relatorio_sem_valor_km(relatorio), [])

    def test_empresa_interna_usa_valor_km_reembolso_control_sul(self):
        empresa = Cliente.objects.create(
            nome="FISCALMAX",
            razao_social="FISCALMAX",
            valor_km=Decimal("1.85"),
            ativo=True,
        )
        calculo = calcular_km_financeiro(Decimal("10.00"), empresa)

        self.assertEqual(calculo["valor_km_cliente"], Decimal("1.3500"))
        self.assertEqual(calculo["valor_cobranca_cliente"], Decimal("13.50"))

        relatorio = self.criar_relatorio("RT-2026-EMPRESA-INTERNA")
        relatorio.cliente = empresa
        relatorio.save(update_fields=["cliente"])
        RelatorioCliente.objects.create(relatorio=relatorio, cliente=empresa)
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("10.00"),
            valor_km=Decimal("1.35"),
        )
        TrechoKMCliente.objects.create(trecho=trecho, cliente=empresa)

        self.assertEqual(clientes_relatorio_sem_valor_km(relatorio), [])

    def test_nao_reembolsavel_com_empresa_interna_nao_exige_participacao_financeira(self):
        empresa = Cliente.objects.create(
            nome="CONTROLSUL",
            razao_social="CONTROLSUL",
            valor_km=None,
            ativo=True,
        )
        relatorio = self.criar_relatorio("RT-2026-NAO-REEMB-INTERNA")
        relatorio.tipo_reembolso = TipoReembolso.NAO_REEMBOLSAVEL
        relatorio.empresa_grupo = EmpresaGrupo.CONTROLSUL
        relatorio.cliente = empresa
        relatorio.save(update_fields=["tipo_reembolso", "empresa_grupo", "cliente"])
        RelatorioCliente.objects.create(relatorio=relatorio, cliente=empresa)
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="outros",
            descricao="Despesa interna",
            valor=Decimal("100.00"),
        )

        erros = validar_integridade_financeira_relatorio(relatorio)

        self.assertNotIn(
            "Existem clientes no relatório sem participação em despesas ou deslocamentos.",
            erros,
        )

    def test_participacao_cliente_considera_rateio_financeiro_mesmo_sem_vinculo_visual(self):
        relatorio = self.criar_relatorio("RT-2026-RATEIO-PARTICIPACAO")
        RelatorioCliente.objects.create(relatorio=relatorio, cliente=self.cliente)
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("10.00"),
            valor_km=Decimal("1.35"),
        )
        TrechoRateioKM.objects.create(
            trecho=trecho,
            cliente=self.cliente,
            km_original=Decimal("10.00"),
            km_final=Decimal("10.00"),
            valor_rateado=Decimal("18.50"),
            km_cliente=Decimal("10.00"),
            valor_km=Decimal("1.8500"),
            valor_calculado=Decimal("18.50"),
            valor_final=Decimal("18.50"),
        )

        erros = validar_integridade_financeira_relatorio(relatorio)

        self.assertNotIn(
            "Existem clientes no relatório sem participação em despesas ou deslocamentos.",
            erros,
        )

    def test_cliente_sem_qualquer_participacao_continua_bloqueado(self):
        cliente_com_movimento = Cliente.objects.create(
            nome="Cliente Com Movimento",
            valor_km=Decimal("1.85"),
        )
        relatorio = self.criar_relatorio("RT-2026-SEM-PARTICIPACAO")
        RelatorioCliente.objects.create(relatorio=relatorio, cliente=self.cliente, ordem=0)
        RelatorioCliente.objects.create(relatorio=relatorio, cliente=cliente_com_movimento, ordem=1)
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="outros",
            descricao="Despesa de outro cliente",
            valor=Decimal("100.00"),
        )
        despesa.clientes_vinculados.create(cliente=cliente_com_movimento)

        erros = validar_integridade_financeira_relatorio(relatorio)

        self.assertIn(
            "Existem clientes no relatório sem participação em despesas ou deslocamentos.",
            erros,
        )

    def test_resumo_financeiro_somente_km_reembolsa_tecnico_a_um_e_trinta_e_cinco(self):
        self.cliente.valor_km = Decimal("1.85")
        self.cliente.save(update_fields=["valor_km"])
        relatorio = self.criar_relatorio("RT-2026-026")
        relatorio.valor_adiantamento = Decimal("0.00")
        relatorio.save(update_fields=["valor_adiantamento"])
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Origem 1",
            destino="Destino 1",
            km=Decimal("26.81"),
            valor_km=Decimal("1.85"),
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=1,
            data="2026-05-02",
            origem="Origem 2",
            destino="Destino 2",
            km=Decimal("26.04"),
            valor_km=Decimal("1.85"),
        )

        self.assertEqual(relatorio.total_km_percorrido, Decimal("52.85"))
        self.assertEqual(relatorio.valor_km_ressarcir, Decimal("71.35"))
        self.assertEqual(relatorio.valor_km_cobrar_cliente, Decimal("97.77"))
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("0.00"))
        self.assertEqual(relatorio.total_a_reembolsar, Decimal("71.35"))

    def test_financeiro_rejeita_trecho_km_individual(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-021")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        trecho = TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_item_financeiro",
                kwargs={
                    "pk": relatorio.pk,
                    "tipo": "trecho",
                    "item_pk": trecho.pk,
                    "acao": "rejeitar",
                },
            ),
            {"motivo_rejeicao": "Deslocamento duplicado."},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        trecho.refresh_from_db()
        self.assertEqual(trecho.status_financeiro, StatusFinanceiroItem.REJEITADO)
        self.assertEqual(trecho.motivo_recusa, "Deslocamento duplicado.")
        self.assertTrue(trecho.rejeitado)
        self.assertEqual(trecho.motivo_rejeicao, "Deslocamento duplicado.")
        self.assertEqual(trecho.rejeitado_por, self.usuario_financeiro)
        self.assertIsNotNone(trecho.rejeitado_em)
        historico_rejeicao = HistoricoRelatorio.objects.get(
            relatorio=relatorio,
            tipo_evento=TipoEventoHistorico.ITEM_REJEITADO,
        )
        self.assertEqual(historico_rejeicao.dados_json["tipo_item"], "trecho")
        self.assertEqual(
            historico_rejeicao.dados_json["motivo"], "Deslocamento duplicado."
        )

    def test_usuario_comum_sem_grupo_financeiro_nao_rejeita_item(self):
        usuario_comum = get_user_model().objects.create_user(
            username="tecnico-item",
            password="senha-teste",
        )
        self.client.force_login(usuario_comum)
        relatorio = self.criar_relatorio("RT-2026-022")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])
        despesa = ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoco",
            valor=Decimal("50.00"),
            quem_pagou="tecnico",
        )

        response = self.client.post(
            reverse(
                "relatorios:relatorio_item_financeiro",
                kwargs={
                    "pk": relatorio.pk,
                    "tipo": "despesa",
                    "item_pk": despesa.pk,
                    "acao": "rejeitar",
                },
            ),
            {"motivo_rejeicao": "Tentativa indevida."},
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        despesa.refresh_from_db()
        self.assertEqual(despesa.status_financeiro, StatusFinanceiroItem.APROVADO)
        self.assertEqual(despesa.motivo_recusa, "")
        self.assertFalse(despesa.rejeitado)
        self.assertEqual(despesa.motivo_rejeicao, "")
        self.assertIsNone(despesa.rejeitado_por)

    def test_relatorio_aprovado_bloqueia_edicao_e_status(self):
        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-007")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.save(update_fields=["status"])

        response_get = self.client.get(
            reverse("relatorios:relatorio_update", kwargs={"pk": relatorio.pk})
        )
        self.assertRedirects(
            response_get,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )

        response_status = self.client.post(
            reverse(
                "relatorios:relatorio_status",
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.REJEITADO},
            )
        )
        self.assertRedirects(
            response_status,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)

    def test_pdf_reembolso_usa_valores_aprovados_e_omite_zerados(self):
        class FakeCSS:
            def __init__(self, filename):
                self.filename = filename

        class FakeHTML:
            rendered_html = ""

            def __init__(self, string, base_url):
                FakeHTML.rendered_html = string
                self.base_url = base_url

            def write_pdf(self, stylesheets):
                self.stylesheets = stylesheets
                return b"%PDF-FAKE"

        relatorio = self.criar_relatorio("RT-2026-014")
        relatorio.status = StatusRelatorio.APROVADO
        relatorio.save(update_fields=["status"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa aprovada parcial",
            valor=Decimal("50.00"),
            valor_aprovado=Decimal("10.00"),
            quem_pagou="tecnico",
        )
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=1,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Despesa zerada",
            valor=Decimal("30.00"),
            valor_aprovado=Decimal("0.00"),
            quem_pagou="tecnico",
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
            valor_km_aprovado=Decimal("2.00"),
        )

        fake_weasyprint = SimpleNamespace(HTML=FakeHTML, CSS=FakeCSS)
        with patch.dict(sys.modules, {"weasyprint": fake_weasyprint}):
            response = self.client.get(
                reverse("relatorios:relatorio_reembolso_pdf", kwargs={"pk": relatorio.pk})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-FAKE")
        self.assertIn("CONTROLSUL GESTÃO EMPRESARIAL", FakeHTML.rendered_html)
        self.assertIn("RELATÓRIO DE REEMBOLSO", FakeHTML.rendered_html)
        self.assertIn("Despesa aprovada parcial", FakeHTML.rendered_html)
        self.assertIn("10,00", FakeHTML.rendered_html)
        self.assertIn("Deslocamento", FakeHTML.rendered_html)
        self.assertIn("200,00", FakeHTML.rendered_html)
        self.assertIn("210,00", FakeHTML.rendered_html)
        self.assertNotIn("Despesa zerada", FakeHTML.rendered_html)
        self.assertNotIn("50,00", FakeHTML.rendered_html)

    def test_pdf_reembolso_exige_relatorio_aprovado(self):
        relatorio = self.criar_relatorio("RT-2026-015")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.save(update_fields=["status"])

        response = self.client.get(
            reverse("relatorios:relatorio_reembolso_pdf", kwargs={"pk": relatorio.pk})
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )

    def test_pdf_interno_renderiza_dados_financeiros_reais(self):
        class FakeCSS:
            def __init__(self, filename):
                self.filename = filename

        class FakeHTML:
            rendered_html = ""

            def __init__(self, string, base_url):
                FakeHTML.rendered_html = string
                self.base_url = base_url

            def write_pdf(self, stylesheets):
                self.stylesheets = stylesheets
                return b"%PDF-INTERNO-FAKE"

        self.client.force_login(self.usuario_financeiro)
        relatorio = self.criar_relatorio("RT-2026-016")
        relatorio.status = StatusRelatorio.CONFERENCIA
        relatorio.motivo_rejeicao = "Ajuste solicitado para conferência interna."
        relatorio.save(update_fields=["status", "motivo_rejeicao"])
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            tipo="alimentacao",
            descricao="Almoço com valor ajustado",
            valor=Decimal("80.00"),
            valor_aprovado=Decimal("50.00"),
            quem_pagou="tecnico",
        )
        ItemDespesa.objects.create(
            relatorio=relatorio,
            ordem=1,
            data="2026-05-02",
            tipo="pedagio",
            descricao="Pedágio sem comprovante",
            valor=Decimal("20.00"),
            rejeitado=True,
            motivo_rejeicao="Comprovante ausente.",
            quem_pagou="tecnico",
        )
        TrechoKm.objects.create(
            relatorio=relatorio,
            ordem=0,
            data="2026-05-02",
            origem="Curitiba",
            destino="Ponta Grossa",
            km=Decimal("100.0"),
            valor_km=Decimal("2.50"),
            valor_km_aprovado=Decimal("2.00"),
        )
        HistoricoRelatorio.objects.create(
            relatorio=relatorio,
            usuario=self.usuario_financeiro,
            acao="Relatório enviado para conferência",
            descricao="Registro usado no PDF interno.",
        )

        fake_weasyprint = SimpleNamespace(HTML=FakeHTML, CSS=FakeCSS)
        with patch.dict(sys.modules, {"weasyprint": fake_weasyprint}):
            response = self.client.get(
                reverse("relatorios:relatorio_pdf_interno", kwargs={"pk": relatorio.pk})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-INTERNO-FAKE")
        self.assertIn("Relatório Financeiro Interno", FakeHTML.rendered_html)
        self.assertIn("Total solicitado", FakeHTML.rendered_html)
        self.assertIn("Total aprovado", FakeHTML.rendered_html)
        self.assertIn("Diferença removida", FakeHTML.rendered_html)
        self.assertIn("Almoço com valor ajustado", FakeHTML.rendered_html)
        self.assertIn("Pedágio sem comprovante", FakeHTML.rendered_html)
        self.assertIn("REJEITADO", FakeHTML.rendered_html)
        self.assertIn("AJUSTADO", FakeHTML.rendered_html)
        self.assertIn("Comprovante ausente.", FakeHTML.rendered_html)
        self.assertIn("Atenções identificadas", FakeHTML.rendered_html)
        self.assertIn("Histórico resumido", FakeHTML.rendered_html)
        self.assertIn("Relatório enviado para conferência", FakeHTML.rendered_html)
        self.assertIn("Gerado por financeiro", FakeHTML.rendered_html)


class EmailServiceTests(TestCase):
    @override_settings(FINANCEIRO_EMAIL="financeiro-central@controlsul.com.br")
    def test_destinatarios_financeiro_usam_email_central(self):
        usuario = get_user_model().objects.create_user(
            username="financeiro.pessoal",
            email="financeiro.pessoal@controlsul.com.br",
            password="x",
        )
        grupo = Group.objects.get(name="Financeiro")
        usuario.groups.add(grupo)

        from relatorios.services.email_service import get_financeiro_recipients

        self.assertEqual(get_financeiro_recipients(), ["financeiro-central@controlsul.com.br"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="sistema@controlsul.com.br",
    )
    def test_envio_base_registra_email_log_enviado(self):
        from relatorios.services.email_service import enviar_email_base

        enviados = enviar_email_base(
            "Assunto teste",
            "Corpo teste",
            ["destino@controlsul.com.br"],
            tipo_email="teste_unitario",
        )

        self.assertEqual(enviados, 1)
        log = EmailLog.objects.get(tipo="teste_unitario")
        self.assertEqual(log.status, "enviado")
        self.assertEqual(log.tentativas, 1)
        self.assertEqual(log.destinatarios, ["destino@controlsul.com.br"])


class IdentidadeAdPreparacaoTests(TestCase):
    @override_settings(
        AD_GROUP_MAPPING={
            "CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local": "Financeiro",
            "ERP-Tecnicos": "Tecnico",
        }
    )
    def test_mapeia_grupos_ad_por_dn_e_nome_simples(self):
        grupos = mapear_grupos_ad_para_django(
            [
                "CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local",
                "ERP-Tecnicos",
                "Grupo sem mapeamento",
            ]
        )

        self.assertEqual(grupos, ["Financeiro", "Tecnico"])

    def test_validacao_aponta_grupo_django_invalido_no_mapeamento(self):
        resultado = validar_mapeamento_grupos_ad(
            {
                "ERP-Financeiro": "Financeiro",
                "ERP-Inexistente": "Grupo que nao existe",
            }
        )

        self.assertFalse(resultado["valido"])
        self.assertEqual(resultado["grupos_invalidos"], ["Grupo que nao existe"])

    def test_sincronizacao_usuario_externo_atualiza_dados_e_grupos_erp(self):
        usuario = get_user_model().objects.create_user(
            username="usuario.ad",
            password="senha-local",
            email="antigo@empresa.local",
        )
        grupo_antigo = Group.objects.get(name="Tecnico")
        grupo_preservado = Group.objects.create(name="Grupo externo preservado")
        usuario.groups.add(grupo_antigo, grupo_preservado)

        resultado = sincronizar_usuario_externo(
            UsuarioExternoSnapshot(
                username="usuario.ad",
                email="usuario@empresa.local",
                first_name="Usuario",
                last_name="AD",
                grupos_ad=("ERP-Financeiro",),
            ),
            mapeamento_grupos={"ERP-Financeiro": "Financeiro"},
        )

        usuario.refresh_from_db()
        grupos = set(usuario.groups.values_list("name", flat=True))
        self.assertTrue(resultado.atualizado)
        self.assertEqual(resultado.grupos_adicionados, ("Financeiro",))
        self.assertEqual(resultado.grupos_removidos, ("Tecnico",))
        self.assertEqual(usuario.email, "usuario@empresa.local")
        self.assertEqual(usuario.first_name, "Usuario")
        self.assertEqual(usuario.last_name, "AD")
        self.assertIn("Financeiro", grupos)
        self.assertNotIn("Tecnico", grupos)
        self.assertIn("Grupo externo preservado", grupos)

    def test_sincronizacao_dry_run_nao_altera_usuario(self):
        usuario = get_user_model().objects.create_user(
            username="dryrun",
            password="senha-local",
            email="original@empresa.local",
        )

        resultado = sincronizar_usuario_externo(
            UsuarioExternoSnapshot(
                username="dryrun",
                email="novo@empresa.local",
                grupos_ad=("ERP-Financeiro",),
            ),
            mapeamento_grupos={"ERP-Financeiro": "Financeiro"},
            dry_run=True,
        )

        usuario.refresh_from_db()
        self.assertTrue(resultado.dry_run)
        self.assertTrue(resultado.atualizado)
        self.assertEqual(usuario.email, "original@empresa.local")
        self.assertFalse(usuario.groups.filter(name="Financeiro").exists())

    def test_normaliza_username_ad_windows_e_upn(self):
        self.assertEqual(normalizar_username_ad("EMPRESA\\Gabriel.Oliveira"), "gabriel.oliveira")
        self.assertEqual(normalizar_username_ad("Gabriel.Oliveira@empresa.local"), "gabriel.oliveira")

    def test_snapshot_ldap_usa_atributos_padrao_do_ad(self):
        snapshot = construir_snapshot_ldap(
            "EMPRESA\\usuario",
            {
                "sAMAccountName": [b"usuario"],
                "mail": [b"usuario@empresa.local"],
                "givenName": [b"Usuario"],
                "sn": [b"Teste"],
                "distinguishedName": [b"CN=Usuario Teste,OU=Users,DC=empresa,DC=local"],
            },
            grupos_ad=("CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local",),
        )

        self.assertEqual(snapshot.username, "usuario")
        self.assertEqual(snapshot.email, "usuario@empresa.local")
        self.assertEqual(snapshot.first_name, "Usuario")
        self.assertEqual(snapshot.last_name, "Teste")
        self.assertEqual(
            snapshot.grupos_ad,
            ("CN=ERP-Financeiro,OU=Grupos,DC=empresa,DC=local",),
        )

    @override_settings(LDAP_AUTH_ENABLED=False)
    def test_backend_ldap_desligado_nao_interfere_no_login_local(self):
        backend = ActiveDirectoryBackend()

        self.assertIsNone(
            backend.authenticate(None, username="usuario", password="senha")
        )

    def test_sincronizacao_ldap_marca_senha_local_como_inutilizavel(self):
        usuario = get_user_model().objects.create_user(
            username="usuario.ldap",
            password="senha-local",
        )
        self.assertTrue(usuario.has_usable_password())

        resultado = sincronizar_usuario_externo(
            UsuarioExternoSnapshot(
                username="usuario.ldap",
                grupos_ad=("ERP-Financeiro",),
            ),
            mapeamento_grupos={"ERP-Financeiro": "Financeiro"},
            marcar_senha_inutilizavel=True,
        )

        usuario.refresh_from_db()
        self.assertFalse(usuario.has_usable_password())
        self.assertTrue(usuario.groups.filter(name="Financeiro").exists())
        self.assertTrue(resultado.usuario_local_migrado)

    def test_backend_bloqueia_fallback_local_quando_usuario_existe_no_ad(self):
        backend = ActiveDirectoryBackend()

        with (
            override_settings(LDAP_AUTH_ENABLED=True),
            patch.object(backend, "_usuario_existe_no_ad", return_value=True),
            patch.object(backend, "_autenticar_em_dcs", return_value=None),
        ):
            with self.assertRaises(PermissionDenied):
                backend.authenticate(None, username="usuario.ad", password="senha-errada")


class IdentidadeAdUtilitariosTests(SimpleTestCase):
    def test_detecta_conta_ad_desativada_bloqueada_e_expirada(self):
        attrs = {
            "userAccountControl": [b"514"],
            "lockoutTime": [b"123456"],
            "accountExpires": [b"1"],
        }

        self.assertTrue(conta_ad_desativada(attrs))
        self.assertTrue(conta_ad_bloqueada(attrs))
        self.assertTrue(conta_ad_expirada(attrs))
        self.assertFalse(usuario_ad_ativo(attrs))

    def test_conta_ad_sem_flags_especiais_fica_ativa(self):
        attrs = {
            "userAccountControl": [b"512"],
            "lockoutTime": [b"0"],
            "accountExpires": [b"0"],
        }

        self.assertFalse(conta_ad_desativada(attrs))
        self.assertFalse(conta_ad_bloqueada(attrs))
        self.assertFalse(conta_ad_expirada(attrs))
        self.assertTrue(usuario_ad_ativo(attrs))

    def test_extrai_domain_users_quando_grupo_primario_do_ad(self):
        grupos = extrair_grupos_ad(attrs={"primaryGroupID": [b"513"]})

        self.assertIn("Domain Users", grupos)

    @override_settings(LDAP_SERVER_URIS=["ldap://dc01", "ldap://dc02"])
    def test_backend_usa_lista_de_dcs_configurada(self):
        from relatorios.services.identidade import ldap_backend

        self.assertEqual(ldap_backend._ldap_server_uris(), ["ldap://dc01", "ldap://dc02"])


class CompletarCadastroUsuarioTests(TestCase):
    def setUp(self):
        self.grupo_tecnico = Group.objects.get(name="Tecnico")

    def criar_usuario(self, completo=False):
        usuario = get_user_model().objects.create_user(
            username="usuario.cadastro",
            password="senha-teste",
            first_name="Usuario" if completo else "",
            last_name="Teste" if completo else "",
            email="usuario.cadastro@example.com" if completo else "",
        )
        usuario.groups.add(self.grupo_tecnico)
        if completo:
            PerfilUsuario.objects.create(
                usuario=usuario,
                cadastro_confirmado_em=timezone.now(),
            )
        return usuario

    def test_usuario_incompleto_e_redirecionado_para_confirmacao(self):
        usuario = self.criar_usuario(completo=False)
        self.client.force_login(usuario)

        response = self.client.get(reverse("relatorios:relatorio_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("relatorios:completar_cadastro"), response["Location"])
        self.assertIn("next=", response["Location"])

    def test_usuario_completo_acessa_url_protegida(self):
        usuario = self.criar_usuario(completo=True)
        self.client.force_login(usuario)

        response = self.client.get(reverse("relatorios:relatorio_list"))

        self.assertEqual(response.status_code, 200)

    def test_confirmacao_salva_dados_e_redireciona_para_next(self):
        usuario = self.criar_usuario(completo=False)
        self.client.force_login(usuario)
        next_url = reverse("relatorios:relatorio_list")

        response = self.client.post(
            f"{reverse('relatorios:completar_cadastro')}?next={next_url}",
            {
                "first_name": "Gabriel",
                "last_name": "Oliveira",
                "email": "gabriel.oliveira@example.com",
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url)
        usuario.refresh_from_db()
        self.assertEqual(usuario.first_name, "Gabriel")
        self.assertEqual(usuario.last_name, "Oliveira")
        self.assertEqual(usuario.email, "gabriel.oliveira@example.com")
        self.assertIsNotNone(usuario.perfil_usuario.cadastro_confirmado_em)
