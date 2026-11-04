const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function validation() {
  const source = fs.readFileSync(path.join(__dirname, '../static/js/custom.js'), 'utf8');
  const context = vm.createContext({document: {addEventListener() {}}, window: {}, setTimeout, clearTimeout});
  vm.runInContext(source.slice(source.indexOf('const FormState ='), source.indexOf('function atualizarBadgesAbas()')) +
    '; globalThis.rules = FieldRules;', context);
  return context.rules.find(rule => String(rule.run).includes('origemDestinoObrigatorio'));
}

function field(name, value, type, feedback) {
  const classes = new Set();
  return {name, value, type,
    classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
    closest: () => ({querySelector: selector => selector === '.erro-inline' ? feedback : null})};
}

for (const role of ['origem', 'destino']) {
  test(`${role}: texto manual valido com coordenadas ocultas vazias`, () => {
    const rule = validation();
    const feedback = {textContent: ''};
    const fields = [field(`trechos-0-${role}`, 'Endereco manual', 'text', feedback),
      ...['_lat', '_lon', '_endereco_completo'].map(s => field(`trechos-0-${role}${s}`, '', 'hidden', feedback))];
    fields.forEach(f => { if (rule.test(f)) rule.run(f); });
    assert.equal(feedback.textContent, '');
    assert.ok(fields.every(f => !f.classList.contains('is-invalid')));
  });

  test(`${role}: campo visivel vazio continua obrigatorio`, () => {
    const rule = validation();
    const feedback = {textContent: ''};
    const visible = field(`trechos-3-${role}`, '   ', 'text', feedback);
    assert.equal(rule.test(visible), true);
    rule.run(visible);
    assert.equal(visible.classList.contains('is-invalid'), true);
    assert.equal(feedback.textContent, 'Campo obrigatório.');
    visible.value = 'Endereco preenchido';
    rule.run(visible);
    assert.equal(visible.classList.contains('is-invalid'), false);
    assert.equal(feedback.textContent, '');
  });
}

test('campos ocultos e nomes apenas semelhantes nao recebem obrigatoriedade de endereco', () => {
  const rule = validation();
  for (const name of ['trechos-0-origem_lat', 'trechos-0-destino_lon', 'trechos-0-origem_endereco_completo', 'outro-origem_extra']) {
    assert.equal(Boolean(rule.test({name, type: 'text'})), false);
  }
  assert.equal(rule.test({name: 'trechos-0-origem', type: 'hidden'}), false);
});
