from decimal import Decimal
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Adiantamento,
    Cliente,
    EmailLog,
    HistoricoRelatorio,
    ItemDespesa,
    PerfilUsuario,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusFinanceiroItem,
    StatusRelatorio,
    Tecnico,
    TipoAdiantamento,
    TipoEventoHistorico,
    TrechoKm,
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
    usuario_eh_administrativo,
    usuario_eh_admin_extra,
    usuario_tem_acesso_total,
)
from .services.resumo_cliente_service import resumo_financeiro_por_cliente
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
            "despesas-TOTAL_FORMS": "0",
            "despesas-INITIAL_FORMS": "0",
            "despesas-MIN_NUM_FORMS": "0",
            "despesas-MAX_NUM_FORMS": "1000",
            "trechos-TOTAL_FORMS": "0",
            "trechos-INITIAL_FORMS": "0",
            "trechos-MIN_NUM_FORMS": "0",
            "trechos-MAX_NUM_FORMS": "1000",
        }

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
        self.assertEqual(relatorio.valor_removido_reembolso, Decimal("117.50"))

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
