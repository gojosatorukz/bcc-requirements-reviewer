import assert from 'node:assert/strict';
import {execFileSync, spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {basename, join, resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const root = fileURLToPath(new URL('../../', import.meta.url));
export const here = fileURLToPath(new URL('./', import.meta.url));
const hosted = 'https://razbor-tz.onrender.com';
const py = process.env.PYTHON || 'python3';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const now = () => new Date().toISOString();
const buttons = {site:'Запустить проверку', local:'Проверить через сервер проекта'};

export function launchBrowser() {
  const {chromium} = createRequire(import.meta.url)(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
  return chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
}

export async function startWorkbench(cwd, home) {
  const python = execFileSync(py, ['-c', 'import sys;print(sys.executable)'], {encoding:'utf8'}).trim();
  const child = spawn(python, ['-m', 'reviewer.serve', '--port', '0'], {cwd, stdio:['ignore', 'pipe', 'inherit'],
    env:{HOME:home, PATH:process.env.PATH, TMPDIR:home, PYTHONUTF8:'1', PYTHONNOUSERSITE:'1'}});
  try {
    const origin = await new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(new Error('Локальный сервер не открыл порт')), 20000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Локальный сервер завершился: ' + code)); });
      createInterface({input:child.stdout}).on('line', line => {
        const url = line.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
        if (url) { clearTimeout(timer); accept(url); }
      });
    });
    return {origin, close:() => new Promise(done => {
      if (child.exitCode !== null || child.signalCode !== null) return done();
      child.once('exit', done);
      child.kill('SIGTERM');
    })};
  } catch (error) { child.kill(); throw error; }
}

export async function checkForm(context, origin, receipt, output, file = join(here, 'bindery-' + receipt.route + '.md')) {
  const route = receipt.route;
  const endpoint = route === 'local' ? '/api/project-runs' : '/api/runs';
  const document = await readFile(file);
  const page = await context.newPage();
  receipt.origin = origin;
  receipt.endpoint = endpoint;
  await page.goto(origin, {timeout:120000, waitUntil:'domcontentloaded'});
  await page.getByText('Загрузить файл', {exact:true}).click();
  await page.locator('#file').setInputFiles(file);
  assert.ok(await page.locator('input[name="mode"][value="full"]').isChecked());
  receipt.upload_sha256 = await page.locator('#file').evaluate(async input => {
    const bytes = await input.files[0].arrayBuffer();
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    return Array.from(hash, byte => byte.toString(16).padStart(2, '0')).join('');
  });
  assert.equal(receipt.upload_sha256, sha(document));
  if (route === 'local') assert.ok(await page.locator('#project-hint').isVisible());
  const pollIds = new Set();
  page.on('request', request => {
    const url = new URL(request.url());
    const match = url.pathname.match(/^\/api\/status\/([a-f0-9]{32})$/);
    if (match && url.origin === origin && request.method() === 'GET') pollIds.add(match[1]);
  });
  const submitted = page.waitForResponse(reply => new URL(reply.url()).pathname === endpoint &&
    reply.request().method() === 'POST', {timeout:120000});
  await page.getByRole('button', {name:buttons[route], exact:true}).click();
  const response = await submitted;
  receipt.submit_status = response.status();
  const sent = response.request();
  const posted = sent.postDataBuffer();
  if (posted?.length) assert.equal(sha(posted), receipt.upload_sha256);
  assert.equal(decodeURIComponent(sent.headers()['x-file-name']), basename(file));
  assert.equal(new URL(response.url()).origin, origin);
  assert.equal(response.status(), 202, 'Задание не принято: HTTP ' + response.status());
  await page.waitForFunction(() => !document.querySelector('#run-links').hidden ||
    document.querySelector('#brand-face')?.dataset.state === 'failed' &&
    !document.querySelector('#status-message')?.hasAttribute('data-running'), null, {timeout:35 * 60000});
  receipt.status_text = await page.locator('#status-message').innerText();
  assert.equal(pollIds.size, 1, 'Браузер должен проверить статус одного нового задания');
  [receipt.job_id] = pollIds;
  const status = await context.request.get(origin + '/api/status/' + receipt.job_id);
  assert.ok(status.ok());
  const job = await status.json();
  receipt.job_state = job.state;
  receipt.execution = job.execution || null;
  assert.equal(job.state, 'done', receipt.status_text);
  assert.equal(job.mode, 'full');
  if (route === 'local') assert.equal(job.execution, 'project');
  for (const [ext, mime] of [['html','text/html'], ['json','application/json']]) {
    const link = page.locator('#run-' + ext);
    assert.ok(await link.isVisible());
    const url = new URL(await link.getAttribute('href'), origin);
    assert.equal(url.origin, origin);
    assert.equal(url.pathname, '/reports/' + receipt.job_id + '/review.' + ext);
    const reply = await context.request.get(url.href);
    assert.equal(reply.status(), 200);
    assert.equal(reply.headers()['content-type'].split(';')[0], mime);
    const bytes = await reply.body();
    await writeFile(join(output, 'review.' + ext), bytes);
    receipt[ext + '_sha256'] = sha(bytes);
  }
  receipt.downloaded_utc = now();
  return page;
}

async function main() {
  const [route, target] = process.argv.slice(2);
  if (!Object.hasOwn(buttons, route) || !target) throw new Error('node eval/hosted-reserve/browser.mjs site|local NEW_OUTPUT_DIRECTORY');
  const output = resolve(target);
  await mkdir(resolve(output, '..'), {recursive:true});
  await mkdir(output); // Preserve previous runs, including failed attempts.
  const temp = await mkdtemp(join(tmpdir(), 'reviewer-remote-test-'));
  const evidence = {route, started_utc:now()};
  let server, browser;
  try {
    let origin = hosted;
    if (route === 'local') {
      const checkout = join(temp, 'checkout');
      execFileSync('git', ['clone', '--quiet', '--shared', '--', root, checkout]);
      assert.equal(execFileSync('git', ['status', '--porcelain'], {cwd:checkout, encoding:'utf8'}), '');
      evidence.checkout_revision = execFileSync('git', ['rev-parse', 'HEAD'], {cwd:checkout, encoding:'utf8'}).trim();
      server = await startWorkbench(checkout, temp);
      origin = server.origin;
    }
    browser = await launchBrowser();
    const context = await browser.newContext({viewport:{width:390,height:844}, serviceWorkers:'block'});
    await checkForm(context, origin, evidence, output);
  } catch (e) {
    evidence.error = e.message;
    process.exitCode = 1;
  } finally {
    evidence.finished_utc = now();
    await writeFile(join(output, 'browser.json'), JSON.stringify(evidence, null, 2) + '\n');
    await browser?.close();
    await server?.close();
    await rm(temp, {recursive:true, force:true});
  }
  if (evidence.error) throw new Error(evidence.error);
  const checked = spawnSync(py, ['-m', 'eval.hosted-reserve.verify', route, output], {cwd:root, encoding:'utf8'});
  if (checked.error) throw checked.error;
  await writeFile(join(output, 'audit.json'), checked.stdout);
  process.stdout.write(checked.stdout);
  process.stderr.write(checked.stderr);
  process.exitCode = checked.status ?? 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
