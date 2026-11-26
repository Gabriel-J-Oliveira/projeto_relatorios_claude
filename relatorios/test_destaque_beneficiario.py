from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase


class DestaqueBeneficiarioTests(SimpleTestCase):
    def test_consulta_nome_e_rotulos_por_saldo(self):
        path = Path(__file__).resolve().parents[1] / "templates/relatorios/relatorio_consulta.html"
        source = path.read_text(encoding="utf-8")
        panel = source.split('<div class="consulta-status-panel">', 1)[1].split('<dl class="small', 1)[0]
        for saldo, rotulo in ((100, "Valor final a reembolsar"), (0, "Saldo final"), (-10, "Valor a devolver à empresa")):
            with self.subTest(saldo=saldo):
                rendered = Template(panel).render(Context({"consulta": {"relatorio": {"tecnico_reembolso_nome": "Pessoa de nome extenso"}, "totais": {"total_a_reembolsar": saldo}}}))
                self.assertIn("Pessoa de nome extenso", rendered)
                self.assertIn(rotulo, rendered)
        self.assertNotIn("Técnico reembolsado</span>", source)

    def test_conferencia_usa_tecnico_do_relatorio_e_atualiza_rotulo(self):
        path = Path(__file__).resolve().parents[1] / "templates/relatorios/relatorio_detail.html"
        source = path.read_text(encoding="utf-8")
        self.assertIn("relatorio.tecnico_reembolso_exibicao", source)
        self.assertIn('id="resultado-financeiro-label"', source)
        self.assertEqual(source.count("atualizarRotuloSaldo(saldo);"), 2)
