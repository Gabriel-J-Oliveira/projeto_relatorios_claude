const assert = require('node:assert/strict');
const {mkdirSync} = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const url = process.env.RELATORIO_URL;
  const [ana, bruno, carla, cliente] = process.env.RELATORIO_IDS.split(',');
  const output = process.env.RELATORIO_SCREENSHOTS;
  const browser = await chromium.launch({executablePath: process.env.RELATORIO_BROWSER});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 900}, ignoreHTTPSErrors: true});
    // Keep the first-visit guide from covering the controls in this fixture.
    await context.route('**/driver.js@*/dist/driver.js.iife.js', route =>
      route.fulfill({contentType: 'text/javascript', body: ''}));
    await context.addCookies([{name: 'sessionid', value: process.env.RELATORIO_SESSION, url}]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, {waitUntil: 'load'});
    await page.addStyleTag({content: 'html { scroll-behavior: auto !important; }'});
    const area = page.locator('#relatorio-participantes');
    const select = page.getByLabel('Quem receberá o reembolso?');
    const hidden = page.locator('[name=tecnico_reembolso]');
    const opts = () => select.locator('option').evaluateAll(nodes => nodes.map(node => node.value));
    const team = (ids) => page.evaluate(values => {
      setTecnicosEquipe(values);
      atualizarBadgesTecnicosEquipe();
    }, ids);
    assert.equal(await select.isDisabled(), true);
    assert.deepEqual(await opts(), ['']);
    await page.evaluate(id => {
      setClientesInput(document.getElementById('id_clientes_relatorio'), [id]);
      atualizarBadgesClientesRelatorio();
    }, cliente);
    const capture = async (teamSize) => {
      if (!output) return;
      mkdirSync(output, {recursive: true});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(value => document.documentElement.setAttribute('data-bs-theme', value), theme);
        await area.evaluate(el => el.scrollIntoView({block: 'center', behavior: 'instant'}));
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await area.screenshot({path: path.join(output, `${teamSize}-${theme}.png`), animations: 'disabled'});
      }
    };
    await area.locator('[data-bs-target="#modalSelecionarTecnicos"]').click();
    await page.locator(`#modalSelecionarTecnicos .tecnico-opcao[data-tecnico-id="${ana}"]`).click();
    await page.locator('#btn-confirmar-tecnicos').click();
    await page.locator('#modalSelecionarTecnicos').waitFor({state: 'hidden'});
    assert.equal(await hidden.inputValue(), ana);
    assert.deepEqual(await opts(), ['', ana]);
    await capture('um-tecnico');

    await team([ana, bruno]);
    assert.equal(await hidden.inputValue(), '');
    assert.deepEqual(await opts(), ['', ana, bruno]);
    assert.match(await page.locator('#tecnico-reembolso-sugestao').innerText(), /Ana Piloto/);
    await capture('multiplos-tecnicos');
    await select.selectOption(bruno);
    assert.equal(await hidden.inputValue(), bruno);
    await team([bruno, ana]);
    assert.equal(await hidden.inputValue(), bruno, 'Reordering must not invalidate a confirmed recipient');
    await page.evaluate(async ([responsavel, apoio]) => {
      for (const name of ['cliente', 'despesas', 'trechos']) {
        document.getElementById('importar-' + name).checked = false;
      }
      relatorioImportacaoSelecionado = {tecnico_id: responsavel, apoio_ids: [apoio]};
      await aplicarImportacaoRelatorio();
    }, [bruno, ana]);
    assert.equal(await hidden.inputValue(), '', 'Imported team requires a fresh choice');
    await select.selectOption(bruno);
    await team([ana, carla]);
    assert.equal(await hidden.inputValue(), '');
    assert.deepEqual(await opts(), ['', ana, carla]);
    await select.selectOption(carla);
    await team([bruno, carla]);
    assert.equal(await hidden.inputValue(), '');
    assert.equal((await page.locator('#tecnico-reembolso-sugestao').innerText()).trim(), '');

    const restored = await context.newPage();
    restored.on('pageerror', error => errors.push(error.message));
    await restored.addInitScript(([responsavel, apoio]) => {
      localStorage.setItem('relatorio-autosave-novo', JSON.stringify({savedAt: new Date().toISOString(),
        entries: [['tecnico_responsavel', responsavel], ['tecnicos_equipe', apoio], ['tecnico_reembolso', apoio]]}));
    }, [ana, bruno]);
    let restoredPrompt = false;
    restored.once('dialog', dialog => { restoredPrompt = true; return dialog.accept(); });
    await restored.goto(url, {waitUntil: 'load'});
    assert.equal(restoredPrompt, true, JSON.stringify(await restored.evaluate(() => ({
      key: document.getElementById('form-relatorio').dataset.autosaveStorageKey,
      enabled: document.getElementById('form-relatorio').dataset.autosaveEnabled,
      autosaveKey: document.getElementById('id_autosave_key').value,
      status: document.getElementById('autosave-status-text').textContent,
      width: document.getElementById('relatorio-participantes').getBoundingClientRect().width,
      viewport: innerWidth
    }))) + errors.join('; '));
    assert.equal(await restored.locator('[name=tecnico_reembolso]').inputValue(), bruno);
    assert.equal(await restored.getByLabel('Quem receberá o reembolso?').inputValue(), bruno);
    await page.setViewportSize({width: 768, height: 1024});
    assert.equal(await area.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    await page.setViewportSize({width: 400, height: 844});
    const width = await area.evaluate(el => ({
      content: el.scrollWidth, viewport: el.clientWidth,
      rows: Array.from(el.children, child => child.getBoundingClientRect().top)
    }));
    assert.ok(width.content <= width.viewport + 1);
    assert.ok(width.rows[0] < width.rows[1] && width.rows[1] < width.rows[2], JSON.stringify(width));
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
