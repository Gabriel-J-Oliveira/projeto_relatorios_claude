"""Authenticated, read-only pages for the financial investigation."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, CharField, DecimalField, F, OuterRef, Subquery, Value, When
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .models import Adiantamento, DespesaRateio, QuemPagou, RelatorioTecnico, StatusFinanceiroItem, StatusRelatorio, TipoDespesa
from .services.autorizacao_service import exigir_acesso_erp, queryset_relatorios_visiveis
from .services.financeiro_analitico_service import ConsultaFinanceira, url_investigacao


def _linhas(consulta):
    atuais=consulta.despesas
    valor=F('valor')
    aprovado=F('valor_aprovado')
    if consulta.cliente_id:
        valor=Subquery(DespesaRateio.objects.filter(despesa_id=OuterRef('pk'),cliente_id=consulta.cliente_id).values('valor_original')[:1])
        aprovado=Subquery(DespesaRateio.objects.filter(despesa_id=OuterRef('pk'),cliente_id=consulta.cliente_id).values('valor_final')[:1])
    campos=('pk','relatorio_id','origem','numero','data','categoria','descricao','valor_lancado','aprovado_analitico','pagador','situacao')
    atuais=atuais.order_by().annotate(origem=Value('atual',output_field=CharField()),
        numero=F('relatorio__numero'),categoria=F('tipo'),valor_lancado=valor,aprovado_analitico=aprovado,
        pagador=F('quem_pagou'),situacao=Case(When(rejeitado=True,then=Value('rejeitado')),default=F('status_financeiro'),output_field=CharField())).values(*campos)
    legadas=consulta.legadas.order_by().annotate(origem=Value('legado',output_field=CharField()),
        numero=F('relatorio__numero_original_legado'),categoria=F('tipo_descricao'),valor_lancado=F('valor'),
        aprovado_analitico=Value(None,output_field=DecimalField()),pagador=Value('',output_field=CharField()),
        situacao=Value('Não informado',output_field=CharField())).values(*campos)
    return atuais.union(legadas,all=True)


@require_GET
@login_required
@exigir_acesso_erp
def financeiro(request, nivel='geral', cliente_id=None, origem=None, pk=None):
    from .views import _relatorios_legados_visiveis
    params=request.GET.copy()
    if cliente_id is not None:
        params['cliente']=str(cliente_id)
    atuais=queryset_relatorios_visiveis(request.user,RelatorioTecnico.objects.all())
    legados=_relatorios_legados_visiveis(request.user)
    relatorio=None
    item=None
    if nivel in ('relatorio','item'):
        if origem not in ('atual','legado'):
            from django.http import Http404
            raise Http404
        if nivel=='item':
            qs=atuais if origem=='atual' else legados
            model=qs.model._meta.get_field('despesas').related_model
            item=get_object_or_404(model.objects.filter(relatorio__in=qs),pk=pk)
            pk=item.relatorio_id
        if origem=='atual':
            relatorio=get_object_or_404(atuais.select_related('cliente','tecnico_responsavel','tecnico_reembolso'),pk=pk)
            atuais=atuais.filter(pk=pk)
            legados=legados.none()
        else:
            relatorio=get_object_or_404(legados,pk=pk)
            legados=legados.filter(pk=pk)
            atuais=atuais.none()
    try:
        consulta=ConsultaFinanceira(atuais,legados,params)
    except ValidationError as exc:
        return render(request,'dashboard/financeiro.html',{'erro_filtros':' '.join(exc.messages),'filtros':params},status=400)
    if item:
        get_object_or_404(consulta.despesas if origem=='atual' else consulta.legadas,pk=item.pk)
    ordenacoes={'data':'data','-data':'-data','valor':'valor_lancado','-valor':'-valor_lancado','descricao':'descricao','categoria':'categoria','numero':'numero'}
    ordenar=params.get('ordem','-data')
    pagina=Paginator(_linhas(consulta).order_by(ordenacoes.get(ordenar,'-data'),'origem','pk'),25).get_page(params.get('page'))
    for linha in pagina.object_list:
        linha['url']=url_investigacao('financeiro_item',params,args=[linha['origem'],linha['pk']])
        linha['relatorio_url']=url_investigacao('financeiro_relatorio',params,args=[linha['origem'],linha['relatorio_id']])
        linha['categoria']=dict(TipoDespesa.choices).get(linha['categoria'],linha['categoria'])
        linha['pagador']=dict(QuemPagou.choices).get(linha['pagador'],linha['pagador'])
        linha['situacao']=dict(StatusFinanceiroItem.choices).get(linha['situacao'],linha['situacao'])
    titulos={'geral':'Financeiro','despesas':'Despesas','cliente':'Cliente','relatorio':'Relatório','item':'Despesa individual','relatorios':'Relatórios e snapshots','adiantamentos':'Adiantamentos e reembolsos'}
    trilha=[{'label':'Financeiro','url':url_investigacao('analiticos',params)}]
    if nivel in ('despesas','cliente','relatorio','item'):
        trilha.append({'label':'Despesas','url':url_investigacao('financeiro_despesas',params)})
    elif nivel=='relatorios':
        trilha.append({'label':'Relatórios e snapshots','url':url_investigacao('financeiro_relatorios',params)})
    elif nivel=='adiantamentos':
        trilha.append({'label':'Adiantamentos e reembolsos','url':url_investigacao('financeiro_adiantamentos',params)})
    if params.get('categoria'):
        trilha.append({'label':dict(TipoDespesa.choices).get(params['categoria'],params['categoria']),
            'url':url_investigacao('financeiro_despesas',params,cliente=None)})
    cliente=None
    if consulta.cliente_id:
        # Obtain identity only through visible linked expense/report records.
        from .models import Cliente
        ids=DespesaRateio.objects.filter(despesa__relatorio__in=consulta.escopo_atuais).values('cliente_id')
        from django.db.models import Q
        cliente=get_object_or_404(Cliente.objects.filter(Q(pk__in=ids)|Q(pk__in=consulta.escopo_legados.values('cliente_vinculado_id'))),pk=consulta.cliente_id)
        trilha.append({'label':cliente.nome,'url':url_investigacao('financeiro_cliente',params,args=[cliente.pk])})
    if relatorio:
        numero=relatorio.numero if origem=='atual' else relatorio.numero_original_legado
        trilha.append({'label':f'Relatório {numero} · {origem}','url':url_investigacao('financeiro_relatorio',params,args=[origem,pk])})
    if item:
        trilha.append({'label':f'Item #{item.pk}','url':request.get_full_path()})
    contexto=dict(titulo_pagina=titulos[nivel],nivel=nivel,filtros=params,opcoes=consulta.opcoes(),
        totais=consulta.totais(),graficos=consulta.graficos() if nivel in ('geral','despesas','cliente') else {},avisos=consulta.avisos,pagina=pagina,
        trilha=trilha,relatorio=relatorio,item=item,cliente=cliente,origem_relatorio=origem,
        despesas_url=consulta.link(),snapshot_url=consulta.link('financeiro_relatorios'),
        adiantamentos_url=consulta.link('financeiro_adiantamentos'),
        graficos_labels=[('categorias','Despesas por categoria'),('empresas','Despesas por empresa · atuais'),
            ('clientes','Clientes · parcelas rateadas / histórico'),('tecnicos','Técnicos responsáveis / histórico'),('status','Distribuição de relatórios por status')],
        anterior_url=request.path+'?'+self_query(params,page=max(1,pagina.number-1)),
        proxima_url=request.path+'?'+self_query(params,page=pagina.number+1))
    if nivel=='relatorios':
        campos=('pk','numero_analitico','fonte','inicio','situacao','solicitado','aprovado')
        atuais=consulta.atuais.order_by().annotate(numero_analitico=F('numero'),fonte=Value('atual',output_field=CharField()),
            inicio=F('data_inicio'),situacao=F('status'),solicitado=F('snapshot_financeiro__total_solicitado'),
            aprovado=F('snapshot_financeiro__total_aprovado')).values(*campos)
        legados=consulta.legados.order_by().annotate(numero_analitico=F('numero_original_legado'),fonte=Value('legado',output_field=CharField()),
            inicio=F('data_inicio'),situacao=Value('legado',output_field=CharField()),
            solicitado=Value(None,output_field=DecimalField()),aprovado=Value(None,output_field=DecimalField())).values(*campos)
        contexto['pagina_relatorios']=Paginator(atuais.union(legados,all=True).order_by('-inicio','fonte','pk'),25).get_page(params.get('page'))
        for registro in contexto['pagina_relatorios']:
            registro['url']=url_investigacao('financeiro_relatorio',params,args=[registro['fonte'],registro['pk']])
            registro['situacao']=dict(StatusRelatorio.choices).get(registro['situacao'],'Histórico legado')
    if nivel=='adiantamentos':
        qs=Adiantamento.objects.filter(relatorio__in=consulta.atuais).select_related('relatorio','tecnico').order_by('-data','pk')
        if consulta.parcial:
            qs=qs.none()
        contexto['pagina_adiantamentos']=Paginator(qs,25).get_page(params.get('page'))
        for registro in contexto['pagina_adiantamentos']:
            registro.investigar_url=url_investigacao('financeiro_relatorio',params,args=['atual',registro.relatorio_id])
    if relatorio:
        from django.urls import reverse
        contexto['original_url']=reverse('relatorios:relatorio_detail' if origem=='atual' else 'relatorios:relatorio_legado_detail',args=[relatorio.pk])
        contexto['trechos']=relatorio.trechos.all() if origem=='atual' else []
        contexto['equipe']=relatorio.tecnicos_adicionais.all() if origem=='atual' else []
        contexto['clientes_relatorio']=relatorio.clientes_vinculados.select_related('cliente') if origem=='atual' else []
    if item and origem=='atual':
        contexto['rateios_item']=item.rateios.select_related('cliente')
        contexto['clientes_item']=item.clientes_vinculados.select_related('cliente')
    pagina_ativa=contexto.get('pagina_relatorios') or contexto.get('pagina_adiantamentos') or pagina
    contexto['pagina_ativa']=pagina_ativa
    contexto['anterior_url']=request.path+'?'+self_query(params,page=max(1,pagina_ativa.number-1))
    contexto['proxima_url']=request.path+'?'+self_query(params,page=pagina_ativa.number+1)
    return render(request,'dashboard/financeiro.html',contexto)


def self_query(params, **changes):
    query=params.copy()
    for campo, valor in changes.items():
        query[campo]=valor
    return query.urlencode()
