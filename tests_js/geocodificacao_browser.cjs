const assert = require('node:assert/strict');
const { mkdirSync } = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const url = process.env.RELATORIO_URL;
  const browser = await chromium.launch({
    executablePath: process.env.RELATORIO_BROWSER,
    ignoreDefaultArgs: ['--hide-scrollbars'],
  });
  try {
    const context = await browser.newContext({ viewport: {width: 1440, height: 900} });
    await context.addCookies([{name: 'sessionid', value: process.env.RELATORIO_SESSION, url}]);
    await context.route('**/driver.js@*/dist/driver.js.iife.js', route => route.fulfill({body: ''}));
    await context.route('**/ArcGIS/rest/services/**', route => route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    let rotas = 0;
    let liberarRota;
    let receberSegundaRota;
    const segundaRota = new Promise(resolve => { receberSegundaRota = resolve; });
    let responderAntiga;
    let receberAntiga;
    const antiga = new Promise(resolve => { receberAntiga = resolve; });
    await page.route('**/mapas/buscar-endereco/**', async route => {
      const query = new URL(route.request().url()).searchParams.get('q');
      if (query === 'Rua antiga') {
        await new Promise(resolve => { responderAntiga = resolve; receberAntiga(); });
      }
      const resultado = {
        display_name: query + ', Cascavel - PR', nome: query, numero: '205',
        bairro: 'Santo Inácio', municipio: 'Cascavel', uf: 'PR',
        lat: -24.9, lon: -53.4, aproximado: true,
      };
      const data = query === 'Rua com muitas opções'
        ? Array.from({length: 5}, (_, i) => ({
          ...resultado, nome: `Opção ${i + 1}: endereço com várias linhas na lista`,
          divergente: true,
        }))
        : [resultado];
      await route.fulfill({json: {success: true, data}});
    });
    await page.route('**/mapas/calcular-rota/**', async route => {
      rotas++;
      if (rotas === 2) {
        await new Promise(resolve => { liberarRota = resolve; receberSegundaRota(); });
      }
      return route.fulfill({json: {success: true, data: {distancia_km: '30.00', geometria: {}}}});
    });
    await page.goto(url, {waitUntil: 'domcontentloaded'});
    await page.locator('[data-bs-target="#tab-km"]').first().click();
    await page.evaluate(() => adicionarLinhaKm());
    const linha = page.locator('.linha-trecho:visible').first();
    const origem = linha.locator('.campo-endereco-mapa[data-map-role="origem"]');
    const destino = linha.locator('.campo-endereco-mapa[data-map-role="destino"]');
    const km = linha.locator('.campo-km');
    await km.fill('42');
    await origem.fill('Rua antiga');
    await antiga;
    await origem.fill('Avenida da FAG');
    const sugestao = linha.getByRole('button', {name: /Avenida da FAG/});
    await sugestao.waitFor();
    const respostaAntiga = page.waitForResponse(r => r.url().includes('Rua+antiga'));
    responderAntiga();
    await respostaAntiga;
    assert.equal(await linha.locator('.endereco-sugestao').count(), 1);
    assert.match(await sugestao.textContent(), /Localização aproximada/);
    const out = process.env.GEOCODIFICACAO_SCREENSHOTS;
    if (out) {
      mkdirSync(out, {recursive: true});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(theme => document.documentElement.setAttribute('data-bs-theme', theme), theme);
        await page.screenshot({path: path.join(out, theme + '.png')});
      }
    }
    await sugestao.click();
    assert.equal(rotas, 0);
    await destino.fill('Rua manual');
    await destino.locator('..').getByRole('button', {name: 'Selecionar ponto no mapa'}).click();
    await page.locator('#mapa-km-formulario').click();
    await page.waitForFunction(() => document.querySelector('.linha-trecho .mapa-km-calculado')?.value === '30.00');
    assert.equal(rotas, 1);
    assert.match(await km.inputValue(), /^42/);
    const snapshot = await page.evaluate(() => ({
      savedAt: new Date().toISOString(),
      entries: Array.from(new FormData(document.getElementById('form-relatorio')))
        .filter(([, value]) => typeof value === 'string'),
      trechosHtml: document.getElementById('corpo-trechos').innerHTML,
    }));
    const restaurada = await context.newPage();
    await restaurada.addInitScript(data => {
      localStorage.setItem('relatorio-autosave-novo', JSON.stringify(data));
    }, snapshot);
    restaurada.once('dialog', dialog => dialog.accept());
    await restaurada.goto(url, {waitUntil: 'load'});
    assert.equal(await restaurada.locator('.linha-trecho .mapa-km-calculado').first().inputValue(), '30.00');
    assert.match(await restaurada.locator('.linha-trecho .mapa-lat[data-map-role="destino"]').first().inputValue(), /^-?\d/);
    await origem.fill('Outro endereço');
    assert.equal(await linha.locator('.mapa-km-calculado').inputValue(), '');
    assert.equal(await linha.locator('.mapa-lat[data-map-role="origem"]').inputValue(), '');
    assert.match(await km.inputValue(), /^42/);
    await linha.getByRole('button', {name: /Outro endereço/}).click();
    let timer;
    try {
      await Promise.race([segundaRota, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("A segunda rota não foi solicitada")), 8000);
      })]);
    } finally {
      clearTimeout(timer);
    }
    await origem.fill('Endereço editado durante o cálculo');
    const respostaRota = page.waitForResponse(r => r.url().includes('/mapas/calcular-rota/'));
    liberarRota();
    await (await respostaRota).finished();
    assert.equal(await linha.locator('.mapa-km-calculado').inputValue(), '');
    assert.match(await km.inputValue(), /^42/);

    await origem.fill('Rua com muitas opções');
    const lista = origem.locator('..').locator('.endereco-sugestoes');
    await lista.getByRole('button', {name: /Opção 5/}).waitFor();
    const cores = [];
    for (const tema of ['light', 'dark']) {
      await page.evaluate(tema => document.documentElement.setAttribute('data-bs-theme', tema), tema);
      const estado = await lista.evaluate(el => ({
        overflow: el.scrollHeight > el.clientHeight,
        largura: getComputedStyle(el).scrollbarWidth,
        cor: getComputedStyle(el).scrollbarColor,
        contencao: getComputedStyle(el).overscrollBehaviorY,
      }));
      assert.equal(estado.overflow, true);
      assert.equal(estado.largura, 'thin');
      assert.equal(estado.contencao, 'contain');
      assert.notEqual(estado.cor, 'auto');
      cores.push(estado.cor);
      if (out) await lista.screenshot({path: path.join(out, `scroll-${tema}.png`)});
    }
    assert.notEqual(cores[0], cores[1], 'A barra deve acompanhar o tema');
    await lista.hover();
    await page.mouse.wheel(0, 300);
    await page.waitForFunction(el => el.scrollTop > 0, await lista.elementHandle());
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
