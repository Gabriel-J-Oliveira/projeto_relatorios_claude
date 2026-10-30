from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_analiticos
from .models import Adiantamento, Cliente, DespesaRateio, ItemDespesa, RelatorioSnapshotFinanceiro


@override_settings(PERMISSOES_CENTRAL_ENABLED=False, EXTRA_ADMIN_USERS=[])
class FinanceiroInvestigativoTests(TestCase):
    def setUp(self):
        test_analiticos.AnaliticosTests.setUp(self)
        self.url=reverse('relatorios:analiticos')
        self.segundo=Cliente.objects.create(nome='Segundo cliente')
        DespesaRateio.objects.create(despesa=self.item,cliente=self.cliente,valor_original='15.10',valor_final='12.00')
        DespesaRateio.objects.create(despesa=self.item,cliente=self.segundo,valor_original='10.00',valor_final='8.00')
        RelatorioSnapshotFinanceiro.objects.create(relatorio=self.relatorio,numero='A1',status='aprovado',
            total_solicitado='100',total_aprovado='80',diferenca_removida='20',checksum='a'*64,
            finalizado_em='2026-01-03T00:00:00Z')
        for tipo,valor,relatorio in [('adiantamento','50',self.relatorio),('reembolso','20',self.relatorio),('adiantamento','30',None)]:
            Adiantamento.objects.create(tecnico=self.tecnico,relatorio=relatorio,tipo=tipo,valor=valor,
                data=date(2026,1,1),descricao=tipo)

    def get(self,nome='analiticos',params=None,args=None):
        resposta=self.client.get(reverse('relatorios:'+nome,args=args),params or {})
        self.assertEqual(resposta.status_code,200)
        return resposta

    def test_indicadores_nao_somam_snapshot_com_itens_ou_rateios(self):
        t=self.get().context['totais']
        self.assertEqual(t['solicitado'],Decimal('100'))
        self.assertEqual(t['aprovado'],Decimal('80'))
        self.assertEqual(t['removido'],Decimal('20'))
        self.assertEqual(t['despesas'],Decimal('35.30'))
        self.assertEqual(t['relatorios'],2)
        self.assertEqual(t['adiantamentos'],Decimal('50'))
        self.assertEqual(t['reembolsos'],Decimal('20'))
        esperado=ItemDespesa.objects.aggregate(t=Sum('valor'))['t']+self.item_legado.valor
        self.assertEqual(t['despesas'],esperado)

    def test_cliente_usa_parcela_e_nao_valor_integral(self):
        resposta=self.get('financeiro_cliente',args=[self.cliente.pk])
        self.assertEqual(resposta.context['totais']['despesas'],Decimal('15.10'))
        self.assertEqual(resposta.context['totais']['vigente'],Decimal('12.00'))
        self.assertIsNone(resposta.context['totais']['solicitado'])
        self.assertIsNone(resposta.context['totais']['adiantamentos'])
        self.assertEqual(list(resposta.context['pagina'])[0]['valor_lancado'],Decimal('15.10'))
        self.assertContains(resposta,'parcela')

    def test_graficos_cliente_nao_atribuem_valor_integral_para_cada_cliente(self):
        grafico=self.get().context['graficos']['clientes']
        atuais=[g['value'] for g in grafico if 'legado' not in g['label']]
        self.assertAlmostEqual(sum(atuais),25.10)

    def test_filtros_periodo_empresa_categoria_tecnico_status_origem(self):
        for params,esperado in [({'inicio':'2026-01-01'},'25.10'),({'origem':'legado'},'10.20'),
            ({'categoria':'alimentacao'},'25.10'),({'status':'legado'},'10.20'),
            ({'tecnico':self.tecnico.pk},'25.10'),({'empresa':'controlsul'},'0')]:
            with self.subTest(params=params):
                self.assertEqual(self.get(params=params).context['totais']['despesas'],Decimal(esperado))
        self.assertEqual(self.client.get(self.url,{'empresa':'inexistente'}).status_code,400)

    def test_categoria_nao_fatia_snapshot(self):
        resposta=self.get(params={'categoria':'alimentacao'})
        self.assertIsNone(resposta.context['totais']['aprovado'])
        self.assertContains(resposta,'Indisponível')

    def test_ausencia_snapshot_nao_e_zero(self):
        RelatorioSnapshotFinanceiro.objects.all().delete()
        self.assertIsNone(self.get().context['totais']['aprovado'])

    def test_legado_nao_ganha_aprovacao_e_continua_investigavel(self):
        resposta=self.get('financeiro_despesas',{'origem':'legado'})
        self.assertEqual(resposta.context['totais']['legado'],Decimal('10.20'))
        self.assertIsNone(resposta.context['totais']['aprovado'])
        self.get('financeiro_relatorio',args=['legado',self.legado.pk])
        self.get('financeiro_item',args=['legado',self.item_legado.pk])

    def test_todas_paginas_breadcrumb_e_contexto(self):
        params={'inicio':'2026-01-01','categoria':'alimentacao'}
        resposta=self.get('financeiro_item',params,args=['atual',self.item.pk])
        self.assertEqual(resposta.context['item'].pk,self.item.pk)
        for passo in resposta.context['trilha']:
            query=parse_qs(urlsplit(passo['url']).query)
            self.assertEqual(query['inicio'],['2026-01-01'])
        self.assertContains(resposta,'Despesa #')
        self.get('financeiro_relatorios')
        self.get('financeiro_adiantamentos')

    def test_post_e_anonimo_bloqueados(self):
        self.assertEqual(self.client.post(self.url).status_code,405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_usuario_comum_nao_alcanca_itens_nem_cliente_por_id(self):
        self.user.groups.clear()
        from django.contrib.auth.models import Group
        self.user.groups.add(Group.objects.create(name='Tecnico'))
        self.relatorio.criado_por=None
        self.relatorio.save(update_fields=['criado_por'])
        for nome,args in [('financeiro_item',['atual',self.item.pk]),('financeiro_relatorio',['atual',self.relatorio.pk]),
            ('financeiro_cliente',[self.cliente.pk]),('financeiro_item',['legado',self.item_legado.pk])]:
            self.assertEqual(self.client.get(reverse('relatorios:'+nome,args=args)).status_code,404)

    def test_rejeicao_zera_valor_vigente_sem_apagar_lancamento(self):
        self.item.rejeitado=True
        self.item.save(update_fields=['rejeitado'])
        t=self.get().context['totais']
        self.assertEqual(t['vigente'],Decimal('0'))
        self.assertEqual(t['rejeitado'],Decimal('25.10'))
        self.assertEqual(t['despesas'],Decimal('35.30'))

    def test_paginacao_e_ordenacao_no_banco(self):
        ItemDespesa.objects.bulk_create([ItemDespesa(relatorio=self.relatorio,tipo='alimentacao',
            descricao=f'Item {i}',valor=Decimal(i+1)) for i in range(30)])
        r=self.get('financeiro_despesas',{'ordem':'-valor','page':2})
        self.assertEqual(len(r.context['pagina']),7)
        self.assertEqual(r.context['pagina'].number,2)
        self.assertIn('ordem=-valor',r.context['anterior_url'])

    def test_query_count_nao_cresce_por_lancamento(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as inicial:
            self.get('financeiro_despesas')
        ItemDespesa.objects.bulk_create([ItemDespesa(relatorio=self.relatorio,tipo='alimentacao',descricao='Teste',valor=1) for _ in range(20)])
        with CaptureQueriesContext(connection) as depois:
            self.get('financeiro_despesas')
        self.assertLessEqual(len(depois),len(inicial)+2)

    def test_situacao_financeira_nao_presume_status_do_legado(self):
        self.item.rejeitado=True
        self.item.save(update_fields=['rejeitado'])
        r=self.get(params={'situacao':'rejeitado'})
        self.assertEqual(r.context['totais']['despesas'],Decimal('25.10'))
        self.assertEqual(r.context['totais']['legado'],Decimal('0'))
        self.assertEqual(self.get('financeiro_cliente',{'situacao':'rejeitado'},args=[self.cliente.pk]).context['totais']['vigente'],Decimal('0'))

    def test_clique_mes_nao_amplia_periodo_original(self):
        r=self.get(params={'inicio':'2026-01-01','fim':'2026-01-02'})
        mes=r.context['graficos']['meses'][0]
        self.assertEqual(parse_qs(urlsplit(mes['url']).query)['fim'],['2026-01-02'])

    def test_cliente_acessivel_sem_dados_no_periodo_mostra_vazio(self):
        r=self.get('financeiro_cliente',{'inicio':'2027-01-01'},args=[self.cliente.pk])
        self.assertEqual(r.context['totais']['despesas'],Decimal('0'))

    def test_payload_grafico_escapa_html(self):
        self.cliente.nome='</script><script>alert(1)</script>'
        self.cliente.save()
        r=self.get()
        self.assertNotContains(r,'</script><script>alert(1)</script>')
        self.assertContains(r,'\\u003C/script\\u003E')

    def test_meses_sem_registro_nao_sao_inventados_como_zero(self):
        meses={g['label']:g for g in self.get().context['graficos']['meses']}
        self.assertIsNone(meses['2025-02']['despesas'])

    @override_settings(PERMISSOES_CENTRAL_ENABLED=True)
    def test_flag_on_nao_usa_grupo_admin_para_relatorio_atual_alheio(self):
        self.relatorio.criado_por=None
        self.relatorio.save(update_fields=['criado_por'])
        resposta=self.get(params={'origem':'atual'})
        self.assertEqual(resposta.context['totais']['despesas'],Decimal('0'))
        self.assertEqual(self.client.get(reverse('relatorios:financeiro_item',args=['atual',self.item.pk])).status_code,404)
