from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase

from .models import Cliente, ItemDespesa, RelatorioTecnico, Tecnico
from .services.consulta_relatorio_service import _montar_consulta_snapshot, montar_consulta_relatorio


class ConsultaReembolsoSnapshotTests(SimpleTestCase):
    def consulta(self, adiantamento="1439.23", liquido="2218.54", usar_saldo_legado=False):
        totais = {
            "total_aprovado": "3657.77", "total_despesas_reembolsaveis": "1788.02",
            "valor_km_ressarcir": "1869.75", "valor_adiantamento": adiantamento,
            "saldo_aprovado": liquido,
        }
        if not usar_saldo_legado:
            totais["total_a_reembolsar"] = liquido
        payload = {"relatorio": {"numero": "185", "identificador": "185", "status": "aprovado"}, "totais": totais}
        snapshot = SimpleNamespace(payload=payload, checksum="checksum-preservado")
        antes = deepcopy(payload)
        consulta = _montar_consulta_snapshot(snapshot)
        request = RequestFactory().get("/relatorios/1/consulta/")
        request.user = AnonymousUser()
        html = render_to_string("relatorios/relatorio_consulta.html", {
            "consulta": consulta, "relatorio": SimpleNamespace(pk=1),
        }, request=request)
        self.assertEqual(snapshot.payload, antes)
        self.assertEqual(snapshot.checksum, "checksum-preservado")
        return consulta, html

    def test_destaque_exibe_liquido_e_nao_aprovado_bruto(self):
        consulta, html = self.consulta()
        self.assertEqual(consulta["totais"].total_a_reembolsar, Decimal("2218.54"))
        self.assertIn('data-testid="reembolso-final">R$ 2218,54', html)
        self.assertNotIn('data-testid="reembolso-final">R$ 3657,77', html)
        self.assertIn('data-testid="adiantamento">R$ 1439,23', html)
        self.assertIn("Despesas reembolsáveis aprovadas", html)
        self.assertIn("KM a ressarcir", html)

    def test_adiantamento_zero(self):
        _, html = self.consulta("0.00", "3657.77")
        self.assertIn('data-testid="adiantamento">R$ 0,00', html)
        self.assertIn('data-testid="reembolso-final">R$ 3657,77', html)

    def test_saldo_zero_preservado(self):
        _, html = self.consulta("3657.77", "0.00")
        self.assertIn('data-testid="reembolso-final">R$ 0,00', html)

    def test_saldo_negativo_nao_e_ocultado(self):
        _, html = self.consulta("4000.00", "-342.23")
        self.assertIn('data-testid="reembolso-final">R$ -342,23', html)
        self.assertIn("Saldo negativo", html)

    def test_snapshot_antigo_com_saldo_aprovado(self):
        _, html = self.consulta(usar_saldo_legado=True)
        self.assertIn('data-testid="reembolso-final">R$ 2218,54', html)


class ConsultaReembolsoDadosVivosTests(TestCase):
    def test_relatorio_existente_sem_snapshot_mantem_calculo_e_pagador(self):
        cliente = Cliente.objects.create(nome="Cliente")
        tecnico = Tecnico.objects.create(nome="Tecnico", email="tecnico@example.com")
        relatorio = RelatorioTecnico.objects.create(
            cliente=cliente, tecnico_responsavel=tecnico, cidade_atendimento="Cascavel",
            data_inicio=date(2026, 5, 1), data_fim=date(2026, 5, 2), motivo="Viagem",
            valor_adiantamento=Decimal("200.00"), status="aprovado",
        )
        ItemDespesa.objects.create(relatorio=relatorio, descricao="Paga pelo tecnico",
            tipo="outros", valor="1000", valor_aprovado="800", quem_pagou="tecnico")
        ItemDespesa.objects.create(relatorio=relatorio, descricao="Paga pela empresa",
            tipo="outros", valor="500", valor_aprovado="500", quem_pagou="empresa")
        ItemDespesa.objects.create(relatorio=relatorio, descricao="Rejeitada",
            tipo="outros", valor="100", rejeitado=True, quem_pagou="tecnico")
        consulta = montar_consulta_relatorio(relatorio)
        self.assertFalse(consulta["usa_snapshot"])
        self.assertEqual(consulta["totais"].valor_adiantamento, Decimal("200"))
        self.assertEqual(consulta["totais"].total_a_reembolsar, Decimal("600"))
        self.assertEqual(consulta["totais"].total_aprovado, Decimal("1300"))
        relatorio.refresh_from_db()
        self.assertEqual(relatorio.valor_adiantamento, Decimal("200"))
