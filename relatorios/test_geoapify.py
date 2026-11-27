from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command

from django.test import SimpleTestCase, override_settings

from .services.maps_service import MapsServiceError, buscar_endereco


@override_settings(MAPS_GEOCODE_PROVIDER="geoapify", GEOAPIFY_API_KEY="chave-ficticia")
class GeoapifyTests(SimpleTestCase):
    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_autocomplete_parametros_e_resultado(self, get, reservar):
        get.return_value = Mock(status_code=200, json=lambda: {"features": [{"properties": {"lat": -24.9, "lon": -53.4, "formatted": "Cascavel, PR", "result_type": "city", "rank": {"confidence": 0.8}}}]})
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            resultado = buscar_endereco("85806-096", contexto="-24.9,-53.4")
        self.assertEqual(resultado[0]["result_type"], "city")
        self.assertEqual(get.call_args.kwargs["params"]["filter"], "countrycode:br")
        self.assertEqual(get.call_args.kwargs["params"]["bias"], "proximity:-53.4,-24.9")
        reservar.assert_called_once()

    @patch("relatorios.services.maps_service.requests.get")
    def test_autocomplete_curto_nao_acessa_rede(self, get):
        self.assertEqual(buscar_endereco("abc"), [])
        get.assert_not_called()

    @override_settings(MAPS_GEOCODE_PROVIDER="nominatim")
    @patch("relatorios.services.maps_service.requests.get")
    def test_nominatim_nao_aceita_autocomplete(self, get):
        with self.assertRaises(MapsServiceError):
            buscar_endereco("Cascavel")
        get.assert_not_called()

    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_429_nao_expoe_chave(self, get, reservar):
        get.return_value = Mock(status_code=429)
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            with self.assertRaises(MapsServiceError) as erro:
                buscar_endereco("Rua exemplo")
        self.assertNotIn("chave-ficticia", str(erro.exception))

    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_busca_estruturada_mantem_resultado_aproximado_como_alternativa(self, get, reservar):
        def resultado(tipo, cidade, confidence, numero=""):
            return Mock(status_code=200, json=lambda: {"features": [{"properties": {
                "lat": -24.9, "lon": -53.4, "formatted": cidade, "result_type": tipo,
                "city": cidade, "street": "Avenida da Fag", "housenumber": numero,
                "postcode": "85806-096", "rank": {"confidence": confidence},
            }}]})

        get.side_effect = [resultado("street", "Toledo", 0.7), resultado("building", "Cascavel", 0.95, "205")]
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            itens = buscar_endereco(
                "Av. da Fag, nº 205 – Santo Inácio, Cascavel - PR, 85806096, Brasil",
                modo="search", localidade="Cascavel - PR",
            )
        self.assertEqual(get.call_args_list[0].kwargs["params"]["text"], "Avenida da Fag, 205 - Santo Inácio, Cascavel - PR, 85806-096")
        params = get.call_args_list[1].kwargs["params"]
        self.assertNotIn("text", params)
        self.assertEqual((params["street"], params["housenumber"], params["city"]), ("Avenida da Fag", "205", "Cascavel"))
        self.assertEqual(reservar.call_count, 2)
        self.assertFalse(itens[0]["aproximado"])
        self.assertTrue(itens[1]["aproximado"] and itens[1]["divergente"])

    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_autocomplete_nao_repete_consulta_e_timeout_nao_expoe_url(self, get, reservar):
        get.side_effect = requests.Timeout("URL contém chave-ficticia")
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            with self.assertRaisesMessage(MapsServiceError, "Serviço de busca de endereço indisponível"):
                buscar_endereco("BR277 KM627", localidade="Cascavel - PR")
        self.assertEqual(get.call_args.kwargs["params"]["text"], "BR-277 km 627")
        reservar.assert_called_once()

    @patch("relatorios.management.commands.diagnostico_geocodificacao.sleep")
    @patch("relatorios.management.commands.diagnostico_geocodificacao.buscar_endereco")
    def test_diagnostico_compara_provedores_sem_autocomplete(self, buscar, pausa):
        buscar.return_value = []
        out = StringIO()
        call_command("diagnostico_geocodificacao", endereco=["BR277 KM 627"], stdout=out)
        self.assertEqual(
            [(c.kwargs["provider"], c.kwargs["modo"]) for c in buscar.call_args_list],
            [("geoapify", "search"), ("nominatim", "search")],
        )
        self.assertIn("nominatim: 0 resultado(s)", out.getvalue())

    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_marco_km_divergente_nao_e_sugerido_mesmo_com_confianca_maxima(self, get, reservar):
        get.return_value = Mock(status_code=200, json=lambda: {"features": [{"properties": {
            "lat": -24.9, "lon": -53.4, "formatted": "Hotel, BR-277 km 588, Cascavel - PR",
            "result_type": "amenity", "rank": {"confidence": 1},
        }}]})
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            self.assertEqual(buscar_endereco("BR277 KM 627", modo="search"), [])
        self.assertEqual(get.call_count, 1)

    @patch("relatorios.services.maps_service._reservar_cota_geoapify")
    @patch("relatorios.services.maps_service.requests.get")
    def test_municipio_digitado_tem_prioridade_sobre_confianca(self, get, reservar):
        get.return_value = Mock(status_code=200, json=lambda: {"features": [
            {"properties": {"lat": -25, "lon": -49, "formatted": cidade, "city": cidade,
                            "result_type": "amenity", "rank": {"confidence": confianca}}}
            for cidade, confianca in (("Curitiba", 1), ("Cascavel", 0.5))
        ]})
        with patch("relatorios.services.maps_service.cache") as cache:
            cache.get.return_value = None
            itens = buscar_endereco("Supermercado Festval, Cascavel")
        self.assertEqual([item["municipio"] for item in itens], ["Cascavel", "Curitiba"])
        self.assertTrue(itens[1]["divergente"] and itens[1]["aproximado"])
        get.assert_called_once()
