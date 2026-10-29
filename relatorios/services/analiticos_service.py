"""Read-only expense lineage. Each amount comes from exactly one source item."""
from datetime import date
from decimal import Decimal
import unicodedata

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.urls import reverse

from relatorios.models import DespesaLegada, ItemDespesa


def _normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', texto or '')
                   if not unicodedata.combining(c)).casefold().strip()


def consultar_despesas(atuais, legados, params):
    """Querysets must already be scoped to the requesting user's visibility."""
    inicio, fim = None, None
    try:
        inicio = date.fromisoformat(params['inicio']) if params.get('inicio') else None
        fim = date.fromisoformat(params['fim']) if params.get('fim') else None
    except ValueError as exc:
        raise ValidationError('Informe datas validas no periodo.') from exc
    if inicio and fim and inicio > fim:
        raise ValidationError('A data inicial deve ser anterior ou igual a final.')
    for campo, valor, operador in [('data_inicio', inicio, 'gte'), ('data_inicio', fim, 'lte')]:
        if valor:
            atuais = atuais.filter(**{f'{campo}__{operador}': valor})
            legados = legados.filter(**{f'{campo}__{operador}': valor})
    origem = params.get('origem', '')
    if origem not in ('', 'atual', 'legado'):
        raise ValidationError('Origem invalida.')
    registros = []
    comuns = ['id', 'relatorio_id', 'data', 'descricao', 'valor', 'relatorio__data_inicio']
    if origem != 'legado':
        itens = ItemDespesa.objects.filter(relatorio__in=atuais).values(
            *comuns, 'tipo', 'quem_pagou', 'numero_documento_comprovante',
            'valor_aprovado', 'relatorio__numero', 'relatorio__tecnico_responsavel__nome',
            'relatorio__cliente__nome', 'relatorio__status')
        tipos = dict(ItemDespesa._meta.get_field('tipo').choices)
        for i in itens.iterator():
            registros.append(dict(
                origem='atual', item=i['id'], relatorio=f"atual:{i['relatorio_id']}",
                numero=i['relatorio__numero'] or f"Rascunho {i['relatorio_id']}",
                categoria=tipos.get(i['tipo'], i['tipo']) or 'Sem categoria',
                tecnico=i['relatorio__tecnico_responsavel__nome'],
                cliente=i['relatorio__cliente__nome'], status=i['relatorio__status'],
                data=i['data'], inicio=i['relatorio__data_inicio'], descricao=i['descricao'],
                valor=i['valor'], aprovado=i['valor_aprovado'],
                documento=i['numero_documento_comprovante'], pagador=i['quem_pagou'],
                url=reverse('relatorios:relatorio_detail', args=[i['relatorio_id']]),
            ))
    if origem != 'atual':
        itens = DespesaLegada.objects.filter(relatorio__in=legados).values(
            *comuns, 'tipo_descricao', 'tipo_codigo', 'documento',
            'relatorio__numero_original_legado', 'relatorio__tecnico_nome', 'relatorio__cliente_nome')
        for i in itens.iterator():
            registros.append(dict(
                origem='legado', item=i['id'], relatorio=f"legado:{i['relatorio_id']}",
                numero=i['relatorio__numero_original_legado'],
                categoria=i['tipo_descricao'] or ('Codigo legado ' + i['tipo_codigo'] if i['tipo_codigo'] else 'Sem categoria'),
                tecnico=i['relatorio__tecnico_nome'], cliente=i['relatorio__cliente_nome'],
                status='Historico legado', data=i['data'], inicio=i['relatorio__data_inicio'],
                descricao=i['descricao'], valor=i['valor'], aprovado=None,
                documento=i['documento'], pagador='',
                url=reverse('relatorios:relatorio_legado_detail', args=[i['relatorio_id']]),
            ))
    busca = _normalizar(params.get('q', ''))
    if busca:
        registros = [r for r in registros if busca in _normalizar(' '.join(
            str(r[c] or '') for c in ('numero', 'descricao', 'cliente', 'tecnico', 'documento')))]
    for r in registros:
        r['chave_categoria'] = _normalizar(r['categoria'])
    def link(**mudancas):
        query = params.copy()
        query.pop('page', None)
        for k, v in mudancas.items():
            if v is None:
                query.pop(k, None)
            else:
                query[k] = str(v)
        return '?' + query.urlencode()
    categorias = {}
    for r in registros:
        chave = r['chave_categoria']
        grupo = categorias.setdefault(chave, dict(nome=r['categoria'], valor=Decimal('0'), quantidade=0))
        grupo['valor'] += r['valor']
        grupo['quantidade'] += 1
        grupo['url'] = link(categoria=chave, relatorio=None, item=None)
    maior = max([abs(g['valor']) for g in categorias.values()] or [Decimal('1')]) or Decimal('1')
    for g in categorias.values():
        g['largura'] = round(float(abs(g['valor']) / maior * 100), 2)
    categoria = params.get('categoria', '')
    selecionados = [r for r in registros if not categoria or r['chave_categoria'] == categoria]
    grupos = {}
    for r in selecionados:
        g = grupos.setdefault(r['relatorio'], dict(numero=r['numero'], origem=r['origem'],
            tecnico=r['tecnico'], valor=Decimal('0'), quantidade=0,
            url=link(relatorio=r['relatorio'], item=None)))
        g['valor'] += r['valor']
        g['quantidade'] += 1
    relatorio = params.get('relatorio', '')
    itens = [r for r in selecionados if r['relatorio'] == relatorio]
    for r in itens:
        r['item_url'] = link(item=r['item'])
    detalhe = next((r for r in itens if str(r['item']) == params.get('item')), None)
    pagina = Paginator(itens if relatorio else sorted(grupos.values(), key=lambda g: g['valor'], reverse=True), 25).get_page(params.get('page'))
    return dict(
        categorias=sorted(categorias.values(), key=lambda g: g['valor'], reverse=True),
        categoria=categoria, relatorio_selecionado=relatorio, detalhe=detalhe,
        relatorio_numero=itens[0]['numero'] if itens else relatorio,
        pagina=pagina,
        total=sum((r['valor'] for r in selecionados), Decimal('0')),
        quantidade=len(selecionados), relatorios_count=len(grupos),
        sem_data=sum(1 for r in selecionados if not r['inicio']),
        somente_legado=sum((r['valor'] for r in selecionados if r['origem']=='legado'), Decimal('0')),
        categoria_url=link(relatorio=None, item=None), inicio_url=link(categoria=None, relatorio=None, item=None),
        anterior_url=link(page=max(1, pagina.number-1)), proxima_url=link(page=pagina.number+1),
        filtros=params,
    )
