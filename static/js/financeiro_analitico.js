(() => {
  'use strict';
  const root = document.getElementById('financial-data');
  if (!root) return;
  const data = JSON.parse(root.textContent) || {};
  const money = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
  const charts = [];
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const navigate = url => { if (url && url.startsWith('/')) window.location.assign(url); };
  function draw() {
    charts.splice(0).forEach(chart => chart.destroy());
    if (!window.ApexCharts) return;
    const style = getComputedStyle(document.documentElement);
    const dark = document.documentElement.dataset.bsTheme === 'dark';
    function render(key, rows, line = false, serie = 'despesas') {
      const el = document.getElementById(`financial-${key}`);
      if (!el) return;
      el.replaceChildren();
      if (!rows.length || (line && rows.every(r => r[serie] == null))) {
        el.textContent = 'Sem dados disponíveis neste recorte.';
        return;
      }
      const chart = new ApexCharts(el, {
        chart: {type: line ? 'line' : 'bar', height: 290, fontFamily: 'inherit', background: 'transparent',
          foreColor: style.getPropertyValue('--bs-body-color').trim(), animations: {enabled: false},
          toolbar: {show: false}, events: {dataPointSelection: (_e, _ctx, config) => {
            const row = rows[config.dataPointIndex];
            navigate(line && serie !== 'despesas' ? row?.snapshot_url : row?.url);
          }}},
        theme: {mode: dark ? 'dark' : 'light'},
        colors: [style.getPropertyValue('--bs-primary').trim()],
        series: [{name: key === 'status' ? 'Relatórios' : 'Valor', data: rows.map(r => line ? r[serie] : r.value)}],
        xaxis: {categories: rows.map(r => safe(r.label))},
        plotOptions: {bar: {horizontal: !line, barHeight: '60%'}},
        dataLabels: {enabled: false}, stroke: {width: line ? 2 : 0, curve: 'straight'},
        markers: {size: line ? 5 : 0},
        grid: {borderColor: style.getPropertyValue('--bs-border-color').trim()},
        tooltip: {shared: false, intersect: true, theme: dark ? 'dark' : 'light', y: {formatter: value => key === 'status' ? `${value} relatórios` : money.format(value)}},
        yaxis: {labels: {formatter: value => line ? money.format(value) : value}},
      });
      charts.push(chart);
      chart.render();
    }
    ['categorias','empresas','clientes','tecnicos','status'].forEach(key => render(key, data[key] || []));
    render('meses', data.meses || [], true, document.getElementById('serie-financeira')?.value || 'despesas');
  }
  document.getElementById('serie-financeira')?.addEventListener('change', draw);
  new MutationObserver(draw).observe(document.documentElement, {attributes: true, attributeFilter: ['data-bs-theme']});
  const order = document.getElementById('ordem');
  if (order) {
    order.value = new URL(window.location.href).searchParams.get('ordem') || '-data';
    order.addEventListener('change', () => {
      const url = new URL(window.location.href);
      url.searchParams.set('ordem', order.value);
      url.searchParams.delete('page');
      window.location.assign(url);
    });
  }
  draw();
})();
