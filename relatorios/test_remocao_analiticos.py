from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse


class RemocaoAnaliticosTests(SimpleTestCase):
    def test_rotas_analiticas_nao_sao_resolvidas(self):
        for path in (
            "/analiticos",
            "/analiticos/explorar/",
            "/analiticos/despesas/",
            "/analiticos/clientes/1/",
            "/analiticos/relatorios/",
            "/analiticos/adiantamentos/",
            "/analiticos/relatorios/atual/1/",
            "/analiticos/despesas/atual/1/",
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_dashboard_principal_permanece(self):
        self.assertEqual(resolve(reverse("relatorios:dashboard")).url_name, "dashboard")
