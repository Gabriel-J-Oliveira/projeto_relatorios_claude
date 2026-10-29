from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, DespesaLegada, ItemDespesa, PerfilUsuario, RelatorioLegado, RelatorioTecnico, Tecnico
from .services.analiticos_service import consultar_despesas


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, EXTRA_ADMIN_USERS=[])
class AnaliticosTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='analista', first_name='Ana', last_name='Teste', email='ana@example.com')
        self.user.groups.add(Group.objects.create(name='Administrador ERP'))
        PerfilUsuario.objects.update_or_create(usuario=self.user, defaults={'cadastro_confirmado_em': timezone.now()})
        self.cliente = Cliente.objects.create(nome='Cliente exemplo')
        self.tecnico = Tecnico.objects.create(nome='Tecnico exemplo', email='tecnico@example.com')
        self.relatorio = RelatorioTecnico.objects.create(
            numero='A1', cliente=self.cliente, tecnico_responsavel=self.tecnico,
            criado_por=self.user, cidade_atendimento='Cascavel', data_inicio=date(2026,1,1),
            data_fim=date(2026,1,2), motivo='Viagem')
        self.item = ItemDespesa.objects.create(relatorio=self.relatorio, tipo='alimentacao',
                                             descricao='Refeicao', valor=Decimal('25.10'))
        self.legado = RelatorioLegado.objects.create(numero_original_legado='A1',
            tecnico_nome='Outro tecnico', data_inicio=date(2025,1,1))
        self.item_legado = DespesaLegada.objects.create(relatorio=self.legado,
            tipo_descricao='Alimentação', valor=Decimal('10.20'), descricao='Jantar antigo')
        self.client.force_login(self.user)
        self.url = reverse('relatorios:analiticos')

    def test_soma_sem_duplicar_e_mantem_identidades_de_origem(self):
        with self.assertNumQueries(2):
            ctx = consultar_despesas(RelatorioTecnico.objects.all(), RelatorioLegado.objects.all(), QueryDict())
        self.assertEqual(ctx['total'], Decimal('35.30'))
        self.assertEqual(ctx['relatorios_count'], 2)
        self.assertEqual(len(ctx['categorias']), 1)

    def test_trilha_categoria_relatorio_item_e_link_original(self):
        resposta = self.client.get(self.url, {'categoria':'alimentacao',
            'relatorio':f'atual:{self.relatorio.pk}', 'item':self.item.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['detalhe']['valor'], Decimal('25.10'))
        self.assertContains(resposta, reverse('relatorios:relatorio_detail', args=[self.relatorio.pk]))
        legado = self.client.get(self.url, {'relatorio':f'legado:{self.legado.pk}', 'item':self.item_legado.pk})
        self.assertEqual(legado.context['detalhe']['origem'], 'legado')

    def test_filtros_busca_periodo_origem_e_datas_invalidas(self):
        self.assertEqual(self.client.get(self.url, {'origem':'legado'}).context['total'], Decimal('10.20'))
        self.assertEqual(self.client.get(self.url, {'inicio':'2026-01-01'}).context['total'], Decimal('25.10'))
        self.assertEqual(self.client.get(self.url, {'q':'jantar'}).context['quantidade'], 1)
        self.assertEqual(self.client.get(self.url, {'inicio':'errado'}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {'inicio':'2026-02-01', 'fim':'2025-01-01'}).status_code, 400)

    def test_usuario_comum_nao_acessa_itens_alheios_mesmo_por_id(self):
        self.user.groups.clear()
        self.user.groups.add(Group.objects.create(name='Tecnico'))
        self.relatorio.criado_por = None
        self.relatorio.save(update_fields=['criado_por'])
        resposta = self.client.get(self.url, {'relatorio':f'atual:{self.relatorio.pk}', 'item':self.item.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.context['detalhe'])
        self.assertEqual(resposta.context['total'], Decimal('0'))

    def test_somente_get_e_login_obrigatorio(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_conteudo_html_e_escapado(self):
        self.item.descricao = '<script>alert(1)</script>'
        self.item.save()
        resposta = self.client.get(self.url, {'relatorio':f'atual:{self.relatorio.pk}'})
        self.assertContains(resposta, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(resposta, '<script>alert(1)</script>')

    def test_paginacao_preserva_filtros_e_total_integral(self):
        ItemDespesa.objects.bulk_create([ItemDespesa(relatorio=self.relatorio,
            tipo='alimentacao', descricao='Item', valor=Decimal('1.00')) for _ in range(30)])
        resposta = self.client.get(self.url, {'origem':'atual', 'categoria':'alimentacao',
            'relatorio':f'atual:{self.relatorio.pk}', 'page':2})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['pagina']), 6)
        self.assertEqual(resposta.context['total'], Decimal('55.10'))
        self.assertIn('origem=atual', resposta.context['anterior_url'])

    @override_settings(PERMISSOES_CENTRAL_ENABLED=True)
    def test_central_on_grupo_admin_sozinho_nao_amplia_visibilidade_atual(self):
        self.relatorio.criado_por = None
        self.relatorio.save(update_fields=['criado_por'])
        resposta = self.client.get(self.url, {'origem':'atual',
            'relatorio':f'atual:{self.relatorio.pk}', 'item':self.item.pk})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['total'], Decimal('0'))
        self.assertIsNone(resposta.context['detalhe'])
