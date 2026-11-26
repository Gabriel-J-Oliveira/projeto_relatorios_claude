from types import SimpleNamespace
from unittest.mock import Mock

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.pdf_interno_service import _relatorio_snapshot, _relatorio_vivo


class PdfInternoVisualTests(SimpleTestCase):
    def test_snapshot_lista_participantes_e_beneficiario(self):
        nomes = ["André da Silva", "João José de Oliveira", "Patrícia Gonçalves"]
        relatorio = _relatorio_snapshot({"relatorio": {"tecnico_reembolso": {"nome": nomes[1]}}, "tecnicos": [{"nome": n} for n in nomes]})
        html = render_to_string("relatorios/pdf/interno.html", {"pdf": SimpleNamespace(relatorio=relatorio, total_reembolso_tecnico=0), "empresa": "CONTROLSUL GESTÃO EMPRESARIAL"})
        for nome in nomes:
            self.assertIn(nome, html)
        self.assertIn("Técnicos participantes", html)
        self.assertIn("Beneficiário do reembolso", html)
        self.assertIn("Saldo zerado", html)
        self.assertIn("CONTROLSUL GESTÃO EMPRESARIAL", html)

    def test_fallback_vivo_unico_tecnico_e_multiplos(self):
        relatorio = Mock()
        relatorio.clientes_exibicao.return_value = []
        relatorio.tecnicos_exibicao.return_value = [SimpleNamespace(nome="Maria")]
        relatorio.cidades_exibicao.return_value = []
        relatorio.tecnico_reembolso_id = None
        relatorio.aprovado_por = None
        contexto = _relatorio_vivo(relatorio)
        self.assertEqual(contexto.tecnico_reembolso_nome, "Maria")
        self.assertTrue(contexto.tecnico_reembolso_fallback_historico)
        self.assertEqual(contexto.tecnicos[0].nome, "Maria")
        relatorio.tecnicos_exibicao.return_value.append(SimpleNamespace(nome="João"))
        contexto = _relatorio_vivo(relatorio)
        self.assertEqual(contexto.tecnico_reembolso_nome, "Não informado")
        self.assertFalse(contexto.tecnico_reembolso_fallback_historico)
        relatorio.tecnico_reembolso_id = 2
        relatorio.tecnico_reembolso.nome = "João"
        contexto = _relatorio_vivo(relatorio)
        self.assertEqual(contexto.tecnico_reembolso_nome, "João")
        self.assertFalse(contexto.tecnico_reembolso_fallback_historico)
