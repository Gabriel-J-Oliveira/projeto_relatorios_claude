from datetime import date
from decimal import Decimal

from django.test import TestCase

from .forms import TrechoKmFormSet
from .models import Cliente, EmpresaGrupo, EmpresaGrupoCliente, RelatorioCliente, RelatorioTecnico, Tecnico
from .services.financeiro_validator import validar_cobertura_clientes_relatorio
from .services.km_financeiro_service import valor_km_cliente_contratual


class KmManualCoberturaTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nome="BL ADV", valor_km=Decimal("1.85"))
        self.tecnico = Tecnico.objects.create(nome="Tecnico", email="tecnico@example.com")
        self.relatorio = RelatorioTecnico.objects.create(
            cliente=self.cliente, tecnico_responsavel=self.tecnico,
            cidade_atendimento="Cascavel", data_inicio=date(2026, 5, 1),
            data_fim=date(2026, 5, 2), motivo="Atendimento",
            tipo_reembolso="nao_reembolsavel", empresa_grupo=EmpresaGrupo.BLAZIUS_E_LORENZETTI,
        )
        RelatorioCliente.objects.create(relatorio=self.relatorio, cliente=self.cliente)

    def vincular(self):
        return EmpresaGrupoCliente.objects.create(
            cliente=self.cliente, empresa_grupo=EmpresaGrupo.BLAZIUS_E_LORENZETTI,
        )

    def test_empresa_canonica_sem_nome_legado_dispensa_cobertura(self):
        self.vincular()
        self.assertEqual(validar_cobertura_clientes_relatorio(self.relatorio), [])

    def test_nome_abreviado_sem_associacao_nao_dispensa_cobertura(self):
        self.assertTrue(validar_cobertura_clientes_relatorio(self.relatorio))

    def test_cliente_externo_continua_exigindo_participacao(self):
        self.vincular()
        externo = Cliente.objects.create(nome="Cliente externo")
        RelatorioCliente.objects.create(relatorio=self.relatorio, cliente=externo, ordem=1)
        self.assertTrue(validar_cobertura_clientes_relatorio(self.relatorio))

    def test_reembolsavel_nao_recebe_dispensa(self):
        self.vincular()
        self.relatorio.tipo_reembolso = "reembolsavel"
        self.assertTrue(validar_cobertura_clientes_relatorio(self.relatorio))

    def test_associacao_inativa_nao_recebe_nova_dispensa(self):
        self.vincular()
        self.cliente.ativo = False
        self.cliente.save(update_fields=["ativo"])
        self.assertTrue(validar_cobertura_clientes_relatorio(self.relatorio))

    def test_reconhecimento_textual_legado_preservado(self):
        self.cliente.nome = "CONTROLSUL"
        self.cliente.save(update_fields=["nome"])
        self.assertEqual(validar_cobertura_clientes_relatorio(self.relatorio), [])

    def test_correcao_cobertura_nao_altera_valor_km(self):
        antes = valor_km_cliente_contratual(self.cliente)
        self.vincular()
        self.assertEqual(valor_km_cliente_contratual(self.cliente), antes)
        self.assertEqual(antes, Decimal("1.8500"))

    def test_formset_aceita_endereco_manual_sem_coordenadas(self):
        dados = {
            "trechos-TOTAL_FORMS": "1", "trechos-INITIAL_FORMS": "0",
            "trechos-MIN_NUM_FORMS": "0", "trechos-MAX_NUM_FORMS": "1000",
            "trechos-0-ordem": "0",
            "trechos-0-data": "2026-05-01", "trechos-0-origem": "Endereco manual de origem",
            "trechos-0-destino": "Endereco manual de destino", "trechos-0-km": "12.50",
            "trechos-0-valor_km": "1.85", "trechos-0-rota_geojson": "{}",
            "trechos-0-clientes": str(self.cliente.pk),
        }
        formset = TrechoKmFormSet(dados, instance=self.relatorio, prefix="trechos")
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertIsNone(formset.forms[0].cleaned_data["origem_lat"])
        self.assertEqual(formset.forms[0].cleaned_data["origem"], dados["trechos-0-origem"])
