from decimal import Decimal
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Adiantamento,
    Cliente,
    ItemDespesa,
    RelatorioTecnico,
    RelatorioTecnicoEquipe,
    StatusRelatorio,
    Tecnico,
    TipoAdiantamento,
    TrechoKm,
)


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
            is_staff=True,
        )

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

        self.assertIn(StatusRelatorio.PENDENTE, choices)
        self.assertIn(StatusRelatorio.APROVADO, choices)
        self.assertIn(StatusRelatorio.REJEITADO, choices)
        self.assertIn(StatusRelatorio.FATURADO, choices)
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

    def test_cria_relatorio_com_despesa_e_trecho_pendente(self):
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
        self.assertEqual(relatorio.status, StatusRelatorio.PENDENTE)
        self.assertEqual(relatorio.despesas.count(), 1)
        self.assertEqual(relatorio.trechos.count(), 1)
        self.assertEqual(relatorio.total_despesas, Decimal("300.00"))
        self.assertEqual(relatorio.numero, "1")

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
        self.assertEqual(novo.valor_adiantamento, Decimal("0.00"))
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
        relatorio.status = StatusRelatorio.PENDENTE
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
        adiantamento = Adiantamento.objects.get(relatorio=relatorio)
        self.assertEqual(adiantamento.tipo, TipoAdiantamento.ADIANTAMENTO)
        self.assertEqual(adiantamento.valor, Decimal("100.00"))
        self.assertEqual(adiantamento.tecnico, self.tecnico)
        self.assertIn(relatorio.numero, adiantamento.descricao)

    def test_usuario_comum_aprova_temporariamente_sem_trava_de_permissao(self):
        usuario_comum = get_user_model().objects.create_user(
            username="tecnico",
            password="senha-teste",
        )
        self.client.force_login(usuario_comum)
        relatorio = self.criar_relatorio("RT-2026-006")
        relatorio.status = StatusRelatorio.PENDENTE
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
        self.assertEqual(despesa.valor_aprovado, Decimal("10.00"))
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertEqual(relatorio.aprovado_por, usuario_comum)

    def test_usuario_anonimo_aprova_temporariamente_sem_aprovador(self):
        relatorio = self.criar_relatorio("RT-2026-012")
        relatorio.status = StatusRelatorio.PENDENTE
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
        self.assertEqual(despesa.valor_aprovado, Decimal("10.00"))
        self.assertEqual(relatorio.status, StatusRelatorio.APROVADO)
        self.assertIsNone(relatorio.aprovado_por)

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
                kwargs={"pk": relatorio.pk, "status": StatusRelatorio.FATURADO},
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
        self.assertIn("CONTROL SUL GESTÃO EMPRESARIAL", FakeHTML.rendered_html)
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
        relatorio.status = StatusRelatorio.PENDENTE
        relatorio.save(update_fields=["status"])

        response = self.client.get(
            reverse("relatorios:relatorio_reembolso_pdf", kwargs={"pk": relatorio.pk})
        )

        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
