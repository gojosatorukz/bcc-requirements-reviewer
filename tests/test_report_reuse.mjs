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
    busy:false, mode:() => 'full', file:null, reuseCheck:null, reuseOffer:null, reportFingerprints:new Map(),
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

test('ten historical fingerprints and demo are fetched once across multiple file changes', async () => {
  const paths = [];
  const ids = Array.from({length:10}, (_, i) => i.toString(16).padStart(32, '0'));
  const ui = harness(async path => {
    paths.push(path);
    return {schema_version:2, run:{...run, input_sha256:path.includes(ids[9]) ? docHash : 'a'.repeat(64)}};
  });
  ui.session.full = ids.map(id => ({id}));
  for (const body of ['unknown 1', 'unknown 2', 'the same bytes', 'unknown 3']) {
    ui.file = documentBytes(body);
    await ui.checkReportReuse();
    assert.equal(ui.get('reuse-report').hidden, body !== 'the same bytes');
  }
  assert.equal(paths.length, 11);
  assert.equal(new Set(paths).size, 11);
  ui.file = documentBytes('the same bytes');
  await ui.checkReportReuse();
  ui.location.assign = target => assert.equal(target, '/reports/' + ids[9] + '/review.html');
  await ui.get('reuse-report-link').click({preventDefault() {}});
  assert.equal(paths.length, 12);
  assert.equal(paths.at(-1), '/reports/' + ids[9] + '/review.json');
});

test('invalid report type and malformed SHA are retried and do not populate the cache', async () => {
  const replies = [{schema_version:1, run}, {schema_version:2, run:{...run, input_sha256:'bad'}}, {schema_version:2, run}];
  const ui = harness(async path => path.includes(runId) ? replies.shift() : null);
  ui.rememberReportFingerprint('demo', {schema_version:2, run:{...run, input_sha256:'0'.repeat(64)}});
  ui.file = documentBytes('the same bytes');
  for (const invalid of [true,true,false]) {
    await ui.checkReportReuse();
    assert.equal(ui.get('reuse-report').hidden, invalid);
    assert.equal(ui.reportFingerprints.has(runId), !invalid);
  }
});
