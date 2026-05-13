from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import (
    Cliente,
    ItemDespesa,
    RelatorioTecnico,
    StatusRelatorio,
    Tecnico,
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

        relatorio = RelatorioTecnico.objects.get(numero="RT-2026-001")
        self.assertRedirects(
            response,
            reverse("relatorios:relatorio_detail", kwargs={"pk": relatorio.pk}),
        )
        self.assertEqual(relatorio.status, StatusRelatorio.PENDENTE)
        self.assertEqual(relatorio.despesas.count(), 1)
        self.assertEqual(relatorio.trechos.count(), 1)
        self.assertEqual(relatorio.total_despesas, Decimal("300.00"))

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

        relatorio = RelatorioTecnico.objects.get(numero="RT-2026-003")
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
