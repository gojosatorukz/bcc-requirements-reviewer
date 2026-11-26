import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const page = readFileSync(new URL('../reviewer/workbench.html', import.meta.url), 'utf8');
const official = JSON.parse(readFileSync(new URL('../eval/executable/runs/official.json', import.meta.url)));

test('search finds owner roles and document numbers and keeps the matching finding selected', () => {
  const controls = new Map();
  const ui = vm.createContext({
    discussion: {findings:official.whole.findings}, renderDiscussion() {},
    Option: class {constructor(text, value) {Object.assign(this, {text, value});}},
    get(id) {
      if (!controls.has(id)) controls.set(id, {
        value:'', replaceChildren(...options) {this.options = options; this.value = options[0]?.value || '';},
      });
      return controls.get(id);
    },
  });
  vm.runInContext(page.slice(page.indexOf('function filterDiscussion('), page.indexOf('function renderDiscussion()')), ui);
  const query = ui.get('discussion-query'), select = ui.get('discussion-finding');
  query.value = 'настроек rest';
  ui.filterDiscussion('D-11');
  assert.equal(select.value, 'D-11');
  assert.ok(select.options.length > 1 && select.options.length < official.whole.findings.length);
  query.value = 'd–02';
  ui.filterDiscussion();
  assert.deepEqual(select.options.map(o => o.value), ['D-02']);
  query.value = 'несуществующее-замечание';
  ui.filterDiscussion();
  assert.equal(select.value, '');
  assert.match(ui.get('discussion-count').textContent, /Совпадений нет/);
});

test('witness progress counts examples independently of completed line groups', () => {
  const controls = new Map();
  const ui = vm.createContext({get(id) {
    if (!controls.has(id)) controls.set(id, {setAttribute(key, value) {this[key] = value;}});
    return controls.get(id);
  }});
  vm.runInContext(page.slice(page.indexOf('function showCoverage('), page.indexOf('async function request(')), ui);
  ui.showCoverage({phase:'executables', total:7, checked:7, proofs_checked:1, proofs_total:2});
  assert.equal(ui.get('coverage').hidden, false);
  assert.equal(ui.get('coverage-meter').value, 1);
  assert.equal(ui.get('coverage-meter').max, 2);
  assert.equal(ui.get('coverage-meter')['aria-valuetext'], 'Готово проверок примеров 1 из 2');
  ui.showCoverage({phase:'executables', total:7, checked:7});
  assert.equal(ui.get('coverage').hidden, true);
  ui.showCoverage({phase:'groups', checked:3, total:7});
  assert.equal(ui.get('coverage-meter').value, 3);
  assert.equal(ui.get('coverage-label').textContent, 'Проверено групп строк 3 из 7');
});
