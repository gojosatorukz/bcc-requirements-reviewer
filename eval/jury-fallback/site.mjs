import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// Opt-in live test. Uploads only one of the two frozen synthetic documents.
const [fixture, output] = process.argv.slice(2);
if (!['packing-v1', 'packing-v2'].includes(fixture) || !output)
  throw new Error('Usage: node eval/jury-fallback/site.mjs packing-v1 OUTPUT_DIRECTORY');
const {chromium} = createRequire(import.meta.url)(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'https://razbor-tz.onrender.com';
const file = fileURLToPath(new URL(fixture + '.md', import.meta.url));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const evidence = {origin:base, fixture, input_sha256:sha256(await readFile(file)), started_utc:new Date().toISOString()};
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
try {
  const context = await browser.newContext({viewport:{width:390,height:844}});
  const page = await context.newPage();
  await page.goto(base, {timeout:120000,waitUntil:'domcontentloaded'});
  await page.locator('input[name="source"][value="file"]').check();
  await page.locator('#file').setInputFiles(file);
  await page.locator('input[name="mode"][value="full"]').check();
  const submitted = page.waitForResponse(r => new URL(r.url()).pathname === '/api/runs' && r.request().method() === 'POST');
  await page.locator('#submit').click();
  const response = await submitted;
  evidence.submit_status = response.status();
  await page.waitForFunction(() => !document.querySelector('#run-links').hidden ||
    !document.querySelector('#run-recovery').hidden, null, {timeout:20 * 60000});
  evidence.status_text = await page.locator('#status-message').innerText();
  if (await page.locator('#run-links').isVisible()) {
    const href = await page.locator('#run-html').getAttribute('href');
    const html = await context.request.get(new URL(href, base).href);
    const json = await context.request.get(new URL(await page.locator('#run-json').getAttribute('href'), base).href);
    assert.ok(html.ok() && json.ok());
    const raw = await json.body(), report = JSON.parse(raw), markup = await html.body();
    assert.equal(report.run.input_sha256, evidence.input_sha256);
    assert.equal(report.schema_version, 2);
    evidence.report = {provider:report.run.provider, model:report.run.model, json_sha256:sha256(raw),
      html_sha256:sha256(markup), fallback_from:report.run.fallback_from || null};
    await mkdir(output,{recursive:true});
    await writeFile(join(output, fixture + '.json'), raw);
    await writeFile(join(output, fixture + '.html'), markup);
  } else {
    const inspected = page.waitForResponse(r => new URL(r.url()).pathname === '/api/structure');
    await page.locator('#recovery-structure').click();
    const structure = await inspected;
    assert.equal(structure.status(), 200);
    evidence.structure = await structure.json();
    evidence.ready_report_visible = await page.locator('#run-recovery a[href="/reports/demo/review.html"]').isVisible();
    process.exitCode = 2;
  }
  evidence.finished_utc = new Date().toISOString();
  await mkdir(output, {recursive:true});
  await writeFile(join(output, fixture + '-site.json'), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify(evidence));
} finally {
  await browser.close();
}
