import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../reviewer/workbench.html', import.meta.url), 'utf8');
const helpers = html.slice(html.indexOf('function clearReportReuse()'), html.indexOf('function setStatus('));
const runId = 'c'.repeat(32);
const docHash = createHash('sha256').update('the same bytes').digest('hex');
const documentBytes = body => new File([body], 'renamed.md', {type:'text/markdown'});
const run = {input_sha256:docHash, started_utc:'2026-11-26T00:01:00Z', agent_version:'abcd', input_name:'original.md'};

function harness(request) {
  const elements = new Map();
  const context = vm.createContext({
    AbortController, crypto:webcrypto, request, isFullReport: report => report.schema_version === 2,
    busy:false, mode:() => 'full', file:null, reuseCheck:null, reuseOffer:null,
    session:{full:[{id:runId}]}, location:{assign:() => assert.fail('unexpected navigation')},
    get(id) {
      if (!elements.has(id)) elements.set(id, {
        hidden:true, removeAttribute(attribute) {delete this[attribute];},
        addEventListener(type, callback) {this[type] = callback;},
      });
      return elements.get(id);
    },
  });
  vm.runInContext(helpers, context);
  return context;
}

test('same bytes under a different name show provenance of the completed report', async () => {
  const ui = harness(async path => {
    assert.equal(path, '/reports/' + runId + '/review.json');
    return {schema_version:2, run};
  });
  ui.file = documentBytes('the same bytes');
  await ui.checkReportReuse();
  assert.equal(ui.get('reuse-report').hidden, false);
  assert.equal(ui.get('reuse-report-link').href, '/reports/' + runId + '/review.html');
  assert.match(ui.get('reuse-report-meta').textContent, /UTC.*abcd.*original\.md/);
});

test('a delayed match for a former file cannot replace a newer selection', async () => {
  let started, resolveOld;
  const oldStarted = new Promise(resolve => {started = resolve;});
  const oldRequest = new Promise(resolve => {resolveOld = resolve;});
  let oldSignal;
  const ui = harness(async (path, {signal}) => {
    if (!oldSignal) {
      oldSignal = signal;
      started();
      return oldRequest;
    }
    return {schema_version:2, run};
  });
  ui.file = documentBytes('the same bytes');
  const first = ui.checkReportReuse();
  await oldStarted;
  ui.file = documentBytes('different bytes');
  await ui.checkReportReuse();
  assert.equal(oldSignal.aborted, true);
  resolveOld({schema_version:2, run});
  await first;
  assert.equal(ui.get('reuse-report').hidden, true);
  assert.equal(ui.get('reuse-report-link').href, undefined);
});

test('a previous run that disappeared while the link was visible is cleared on click', async () => {
  let exists = true;
  const ui = harness(async () => {
    if (!exists) throw Object.assign(new Error('missing'), {status:404});
    return {schema_version:2, run};
  });
  ui.file = documentBytes('the same bytes');
  await ui.checkReportReuse();
  exists = false;
  await ui.get('reuse-report-link').click({preventDefault() {}});
  assert.equal(ui.get('reuse-report').hidden, true);
  assert.equal(ui.get('reuse-report-link').href, undefined);
});
