"""Financial read models. No writes, policy evaluation or snapshot reconstruction."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Case, Count, DecimalField, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncMonth
from django.urls import reverse

from relatorios.models import (
    Adiantamento, Cliente, DespesaLegada, DespesaRateio, EmpresaGrupo,
    ItemDespesa, RelatorioSnapshotFinanceiro, StatusRelatorio, Tecnico, TipoDespesa,
)

MONEY = DecimalField(max_digits=18, decimal_places=2)
ZERO = Decimal('0')


def soma(qs, campo):
    return qs.aggregate(total=Sum(campo))['total'] or ZERO


def url_investigacao(nome, params, args=None, **mudancas):
    query = params.copy()
    query.pop('page', None)
    for campo, valor in mudancas.items():
        query.pop(campo, None)
        if valor not in (None, ''):
            query[campo] = str(valor)
    return reverse('relatorios:' + nome, args=args) + ('?' + query.urlencode() if query else '')


class ConsultaFinanceira:
    def __init__(self, atuais, legados, params):
        self.params = params
        self.escopo_atuais, self.escopo_legados = atuais, legados
        self.avisos = []
        try:
            inicio = date.fromisoformat(params['inicio']) if params.get('inicio') else None
            fim = date.fromisoformat(params['fim']) if params.get('fim') else None
            if inicio and fim and inicio > fim:
                raise ValueError()
            self.cliente_id = int(params['cliente']) if params.get('cliente') else None
            tecnico_id = int(params['tecnico']) if params.get('tecnico') else None
            if any(v is not None and not 0 < v < 2**63 for v in (self.cliente_id, tecnico_id)):
                raise ValueError()
        except (ValueError, OverflowError) as exc:
            raise ValidationError('Confira o período e os identificadores dos filtros.') from exc
        origem, empresa, status = (params.get(k, '') for k in ('origem', 'empresa', 'status'))
        if origem not in ('', 'atual', 'legado') or empresa not in ('', *EmpresaGrupo.values) or status not in ('', 'legado', *StatusRelatorio.values):
            raise ValidationError('Origem, empresa ou status inválido.')
        for data, op in ((inicio, 'gte'), (fim, 'lte')):
            if data:
                atuais = atuais.filter(**{'data_inicio__' + op: data})
                legados = legados.filter(**{'data_inicio__' + op: data})
        if empresa:
            atuais = atuais.filter(empresa_grupo=empresa)
            legados = legados.none()
            self.avisos.append('Empresa do grupo não é inferida pelo texto do escritório legado; este filtro considera apenas atuais.')
        if tecnico_id:
            atuais = atuais.filter(tecnico_responsavel_id=tecnico_id)
            legados = legados.filter(tecnico_vinculado_id=tecnico_id)
            self.avisos.append('Técnico identifica o responsável nos atuais e o vínculo cadastral nos legados; não distribui custos entre participantes.')
        if status:
            atuais = atuais.none() if status == 'legado' else atuais.filter(status=status)
            legados = legados if status == 'legado' else legados.none()
        if origem == 'atual':
            legados = legados.none()
        if origem == 'legado':
            atuais = atuais.none()
        if params.get('sem_empresa'):
            atuais = atuais.filter(empresa_grupo='')
            legados = legados.none()
        if params.get('sem_tecnico'):
            atuais = atuais.none()
            legados = legados.filter(tecnico_vinculado__isnull=True)
        if params.get('sem_cliente'):
            atuais = atuais.none()
            legados = legados.filter(cliente_vinculado__isnull=True)
        self.despesas = ItemDespesa.objects.filter(relatorio__in=atuais)
        self.legadas = DespesaLegada.objects.filter(relatorio__in=legados)
        if params.get('sem_rateio'):
            self.despesas = self.despesas.exclude(pk__in=DespesaRateio.objects.values('despesa_id'))
            self.legadas = self.legadas.none()
        situacao = params.get('situacao', '')
        if situacao not in ('', 'aprovado', 'rejeitado'):
            raise ValidationError('Situação financeira do item inválida.')
        if situacao:
            condicao=Q(rejeitado=True) | Q(status_financeiro='rejeitado')
            self.despesas=self.despesas.filter(condicao) if situacao=='rejeitado' else self.despesas.exclude(condicao)
            self.legadas=self.legadas.none()
        categoria = params.get('categoria', '')
        if categoria:
            if categoria.startswith('legado:'):
                self.despesas = self.despesas.none()
                self.legadas = self.legadas.filter(tipo_descricao=categoria[7:])
            elif categoria in TipoDespesa.values:
                self.despesas = self.despesas.filter(tipo=categoria)
                self.legadas = self.legadas.none()
            else:
                raise ValidationError('Categoria inválida.')
        busca = params.get('q', '').strip()
        if busca:
            self.despesas = self.despesas.filter(Q(descricao__icontains=busca) | Q(relatorio__numero__icontains=busca) | Q(numero_documento_comprovante__icontains=busca))
            self.legadas = self.legadas.filter(Q(descricao__icontains=busca) | Q(relatorio__numero_original_legado__icontains=busca) | Q(documento__icontains=busca))
        if self.cliente_id:
            parcelas = DespesaRateio.objects.filter(cliente_id=self.cliente_id)
            self.despesas = self.despesas.filter(pk__in=parcelas.values('despesa_id'))
            self.legadas = self.legadas.filter(relatorio__cliente_vinculado_id=self.cliente_id)
            self.avisos.append('Neste recorte, valores atuais representam a parcela rateada do cliente. Legados usam o cliente cadastral vinculado ao relatório.')
        # Item filters select reports with matching items; no joins can multiply snapshots.
        if categoria or busca or self.cliente_id or params.get('sem_rateio') or situacao:
            atuais = atuais.filter(pk__in=self.despesas.values('relatorio_id'))
            legados = legados.filter(pk__in=self.legadas.values('relatorio_id'))
        self.atuais, self.legados = atuais, legados
        self.snapshots = RelatorioSnapshotFinanceiro.objects.filter(relatorio__in=atuais)
        self.parcial = bool(categoria or busca or self.cliente_id or situacao or params.get('sem_rateio'))
        if self.parcial:
            self.avisos.append('Snapshots e adiantamentos não são fracionados por item/cliente; esses indicadores ficam indisponíveis neste recorte.')

    def link(self, nome='financeiro_despesas', **mudancas):
        return url_investigacao(nome, self.params, **mudancas)

    def parcelas(self):
        qs = DespesaRateio.objects.filter(despesa__in=self.despesas)
        return qs.filter(cliente_id=self.cliente_id) if self.cliente_id else qs

    def totais(self):
        despesas = soma(self.parcelas(), 'valor_original') if self.cliente_id else soma(self.despesas, 'valor')
        despesas += soma(self.legadas, 'valor')
        rejeitadas = self.despesas.filter(Q(rejeitado=True) | Q(status_financeiro='rejeitado'))
        if self.cliente_id:
            aprovado = soma(self.parcelas().exclude(despesa__in=rejeitadas), 'valor_final')
            rejeitado = soma(self.parcelas().filter(despesa__in=rejeitadas), 'valor_original')
        else:
            aprovado = soma(self.despesas.annotate(valor_vigente=Case(
                When(Q(rejeitado=True) | Q(status_financeiro='rejeitado'), then=Value(ZERO)),
                default=Coalesce('valor_aprovado', 'valor'), output_field=MONEY)), 'valor_vigente')
            rejeitado = soma(rejeitadas, 'valor')
        n = self.despesas.count() + self.legadas.count()
        adiantamentos = Adiantamento.objects.filter(relatorio__in=self.atuais)
        n_snapshots = self.snapshots.count()
        return dict(despesas=despesas, vigente=aprovado, rejeitado=rejeitado, itens=n,
            media=despesas/n if n else None, relatorios=self.atuais.count()+self.legados.count(),
            solicitado=None if self.parcial or not n_snapshots else soma(self.snapshots, 'total_solicitado'),
            aprovado=None if self.parcial or not n_snapshots else soma(self.snapshots, 'total_aprovado'),
            removido=None if self.parcial or not n_snapshots else soma(self.snapshots, 'diferenca_removida'),
            snapshots=n_snapshots,
            adiantamentos=None if self.parcial else soma(adiantamentos.filter(tipo='adiantamento'), 'valor'),
            reembolsos=None if self.parcial else soma(adiantamentos.filter(tipo='reembolso'), 'valor'),
            legado=soma(self.legadas, 'valor'))

    def graficos(self):
        # Each aggregation is performed at its own grain in SQL.
        base = self.parcelas() if self.cliente_id else self.despesas
        prefixo = 'despesa__' if self.cliente_id else ''
        valor = 'valor_original' if self.cliente_id else 'valor'
        def barras(campo, filtro, labels=None):
            grupos = base.order_by().values(prefixo+campo).annotate(total=Sum(valor)).order_by('-total')
            return [dict(label=(labels or {}).get(g[prefixo+campo], g[prefixo+campo]) or 'Não informado',
                value=float(g['total']), url=self.link(**{filtro:g[prefixo+campo]})) for g in grupos]
        categorias = barras('tipo', 'categoria', dict(TipoDespesa.choices))
        for g in self.legadas.order_by().values('tipo_descricao').annotate(total=Sum('valor')):
            categorias.append(dict(label=(g['tipo_descricao'] or 'Sem categoria')+' · legado',
                value=float(g['total']), url=self.link(categoria='legado:'+g['tipo_descricao'])))
        empresas = barras('relatorio__empresa_grupo', 'empresa', dict(EmpresaGrupo.choices))
        # Empty company is unknown, never a link that accidentally clears a filter.
        for g in empresas:
            if g['label']=='Não informado':
                g['url']=self.link(origem='atual',sem_empresa=1)
        clientes = []
        for g in self.parcelas().order_by().values('cliente_id','cliente__nome').annotate(total=Sum('valor_original')).order_by('-total')[:15]:
            clientes.append(dict(label=g['cliente__nome'],value=float(g['total']),
                url=url_investigacao('financeiro_cliente',self.params,args=[g['cliente_id']])))
        for g in self.legadas.order_by().values('relatorio__cliente_vinculado_id','relatorio__cliente_vinculado__nome').annotate(total=Sum('valor')).order_by('-total')[:15]:
            cid=g['relatorio__cliente_vinculado_id']
            clientes.append(dict(label=(g['relatorio__cliente_vinculado__nome'] or 'Sem vínculo cadastral')+' · legado',value=float(g['total']),
                url=url_investigacao('financeiro_cliente', self.params,args=[cid]) if cid else self.link(origem='legado',sem_cliente=1)))
        sem_rateio=self.despesas.exclude(pk__in=DespesaRateio.objects.values('despesa_id'))
        if sem_rateio.exists():
            clientes.append(dict(label='Sem rateio cadastrado · atuais',value=float(soma(sem_rateio,'valor')),url=self.link(sem_rateio=1,origem='atual')))
        tecnicos=[]
        for g in base.order_by().values(prefixo+'relatorio__tecnico_responsavel_id',prefixo+'relatorio__tecnico_responsavel__nome').annotate(total=Sum(valor)).order_by('-total')[:15]:
            tecnicos.append(dict(label=g[prefixo+'relatorio__tecnico_responsavel__nome'],value=float(g['total']),url=self.link(tecnico=g[prefixo+'relatorio__tecnico_responsavel_id'])))
        for g in self.legadas.order_by().values('relatorio__tecnico_vinculado_id','relatorio__tecnico_vinculado__nome').annotate(total=Sum('valor')).order_by('-total')[:15]:
            tid=g['relatorio__tecnico_vinculado_id']
            tecnicos.append(dict(label=(g['relatorio__tecnico_vinculado__nome'] or 'Sem vínculo cadastral')+' · legado',value=float(g['total']),url=self.link(tecnico=tid,origem='legado',sem_tecnico=1 if not tid else None)))
        statuses=[dict(label=dict(StatusRelatorio.choices).get(g['status'],g['status']), value=g['total'],url=self.link(status=g['status']))
            for g in self.atuais.order_by().values('status').annotate(total=Count('pk'))]
        if self.legados.exists():
            statuses.append(dict(label='Histórico legado',value=self.legados.count(),url=self.link(status='legado')))
        meses={}
        def serie(qs, campo, valor_campo, chave):
            for g in qs.order_by().annotate(mes=TruncMonth(campo)).values('mes').annotate(total=Sum(valor_campo)):
                if g['mes']:
                    mes=g['mes'].strftime('%Y-%m')
                    meses.setdefault(mes,{'despesas':0,'solicitado':None,'aprovado':None})
                    meses[mes][chave]=(meses[mes][chave] or 0)+float(g['total'])
        serie(base,prefixo+'relatorio__data_inicio',valor,'despesas')
        serie(self.legadas,'relatorio__data_inicio','valor','despesas')
        if not self.parcial:
            serie(self.snapshots,'relatorio__data_inicio','total_solicitado','solicitado')
            serie(self.snapshots,'relatorio__data_inicio','total_aprovado','aprovado')
        if meses:
            ano, numero = map(int, min(meses).split('-'))
            ultimo=max(meses)
            while f'{ano:04d}-{numero:02d}' < ultimo:
                meses.setdefault(f'{ano:04d}-{numero:02d}',{'despesas':None,'solicitado':None,'aprovado':None})
                numero+=1
                if numero==13:
                    ano,numero=ano+1,1
        return dict(categorias=categorias,empresas=empresas,clientes=clientes,tecnicos=tecnicos,status=statuses,
            meses=[dict(label=m,**meses[m],
                url=self.link(inicio=max(m+'-01',self.params.get('inicio') or m+'-01'),fim=min(self._fim_mes(m),self.params.get('fim') or self._fim_mes(m))),
                snapshot_url=self.link('financeiro_relatorios',inicio=max(m+'-01',self.params.get('inicio') or m+'-01'),fim=min(self._fim_mes(m),self.params.get('fim') or self._fim_mes(m)))) for m in sorted(meses)])

    @staticmethod
    def _fim_mes(mes):
        import calendar
        ano, numero = map(int, mes.split('-'))
        return f'{mes}-{calendar.monthrange(ano,numero)[1]}'

    def opcoes(self):
        # Only expose identities in the user's filtered visibility scope.
        ids_clientes=DespesaRateio.objects.filter(despesa__relatorio__in=self.escopo_atuais).values('cliente_id')
        return dict(empresas=EmpresaGrupo.choices, categorias=TipoDespesa.choices, statuses=StatusRelatorio.choices,
            clientes=Cliente.objects.filter(Q(pk__in=ids_clientes) | Q(pk__in=self.escopo_legados.values('cliente_vinculado_id'))).order_by('nome'),
            tecnicos=Tecnico.objects.filter(Q(pk__in=self.escopo_atuais.values('tecnico_responsavel_id')) | Q(pk__in=self.escopo_legados.values('tecnico_vinculado_id'))).order_by('nome'))
