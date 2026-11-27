import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../reviewer/workbench.html', import.meta.url), 'utf8');
const importer = html.slice(html.indexOf('let importing;'), html.indexOf('function rememberBaselineVersion('));
const id = 'a'.repeat(32), digest = 'd'.repeat(64);
const saved = {schema_version:2, run:{input_name:'Исходное ТЗ.md'}};

function harness(request) {
  const nodes = new Map(), opened = [];
  const ui = vm.createContext({
    AbortController, request, idPattern:/^[0-9a-f]{32}$/, reportHash:/^[0-9a-f]{64}$/,
    isFullReport:r => r.schema_version === 2, demoLoad:Promise.resolve(), requestedGuidedId:null,
    session:{last:{id:'b'.repeat(32)},full:[]}, save(){}, refreshBaselines(){}, checkReportReuse(){},
    publishReport(report, identifier, sha) {opened.push([report, identifier, sha]);},
    get(key) {
      if (!nodes.has(key)) nodes.set(key, {
        classList:{add(){},remove(){}}, addEventListener(event, handler){this[event] = handler;},
        focus(){}, scrollIntoView(){},
      });
      return nodes.get(key);
    },
  });
  vm.runInContext(importer, ui);
  return {ui, opened, choose:name => ui.get('import-file').change({target:{files:[{name,size:123}]}})};
}

test('a delayed response for a previously selected file cannot replace the active report', async () => {
  let finish, firstSignal;
  const stale = new Promise(resolve => {finish = resolve;});
  const {ui, opened, choose} = harness(async (path, options) => {
    if (path === '/api/import-report') {
      if (options.body.name === 'old.json') {
        firstSignal = options.signal;
        await stale; // A server can finish processing after the browser aborts.
      }
      return {id, sha256:digest};
    }
    return {sha:digest,value:saved};
  });
  const slow = choose('old.json');
  await choose('new.json');
  finish();
  await slow;
  assert.equal(firstSignal.aborted, true);
  assert.equal(opened.length, 1);
  assert.deepEqual(opened[0], [saved,id,digest]);
  assert.equal(ui.session.last.stayWith, id);
  assert.equal(ui.session.full[0].name, saved.run.input_name);
});

test('a digest mismatch preserves the active context and offers a recoverable error', async () => {
  const {ui, opened, choose} = harness(async path => path === '/api/import-report' ? {id,sha256:digest}
    : {sha:'e'.repeat(64),value:saved});
  await choose('mismatched.json');
  assert.deepEqual(opened, []);
  assert.equal(ui.session.full.length, 0);
  assert.match(ui.get('import-state').textContent, /Отчёт не открыт.*не совпадает/);
});
