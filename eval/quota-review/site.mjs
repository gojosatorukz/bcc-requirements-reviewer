import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {launchBrowser} from '../hosted-reserve/browser.mjs';

const stage = process.argv[2];
assert.ok(['before', 'after', 'control'].includes(stage));
const origin = 'https://razbor-tz.onrender.com';
const name = stage === 'control' ? 'reservations-fixed.md' : 'reservations.md';
const path = fileURLToPath(new URL(name, import.meta.url));
const digest = createHash('sha256').update(await readFile(path)).digest('hex');
const browser = await launchBrowser();
try {
  const page = await browser.newPage({viewport:{width:390,height:844}});
  await page.goto(origin, {timeout:120000});
  await page.getByText('Загрузить файл', {exact:true}).click();
  await page.locator('#file').setInputFiles(path);
  const accepted = page.waitForResponse(r=>new URL(r.url()).pathname === '/api/runs' && r.request().method() === 'POST');
  await page.locator('#submit').click();
  const response = await accepted;
  assert.equal(response.status(), 202);
  const outcome = {stage,origin,filename:name,sha256:digest,accepted:response.status()};
  if (stage === 'before') {
    await page.locator('#run-recovery').waitFor({timeout:600000});
    outcome.status = await page.locator('#status-message').innerText();
    assert.match(outcome.status,/лимит|квот/i);
    await page.locator('#recovery-structure').click();
    await page.locator('#structure-counts').waitFor();
    outcome.structure = await page.locator('#structure-result').innerText();
    outcome.report_links = await page.locator('#run-links').isVisible();
    assert.equal(outcome.report_links,false);
  } else {
    const panel = page.locator('#partial-report');
    await panel.waitFor({timeout:600000});
    outcome.status = await page.locator('#status-message').innerText();
    outcome.summary = await panel.innerText();
    const popup = page.waitForEvent('popup');
    await page.locator('#partial-html').click();
    const report = await popup;
    await report.locator('h1').waitFor();
    const jsonPath = await page.locator('#partial-json').getAttribute('href');
    const json = await page.request.get(new URL(jsonPath,origin).href);
    assert.ok(json.ok());
    const raw = await json.body();
    const parsed = JSON.parse(raw);
    assert.equal(parsed.input_sha256,digest);
    assert.equal(parsed.report_type,'partial');
    assert.equal(parsed.findings.length,stage === 'after' ? 3 : 0);
    outcome.questions = await report.locator('.question').allTextContents();
    assert.deepEqual(outcome.questions,parsed.findings.map(item=>item.question));
    if (stage === 'after') {
      assert.deepEqual(parsed.findings.map(f=>f.evidence.map(e=>e.line)),[[7,8],[13],[18]]);
      await report.locator('a[href="#line-8"]').click();
      assert.match(await report.locator(':target').innerText(),/Назначить смену B/);
    }
    await writeFile(new URL(stage+'.json',import.meta.url),raw);
    await writeFile(new URL(stage+'.html',import.meta.url),await (await page.request.get(report.url().split('#')[0])).body());
  }
  outcome.checked_utc = new Date().toISOString();
  await writeFile(new URL(stage+'-site.json',import.meta.url),JSON.stringify(outcome,null,2)+'\n');
  console.log(outcome);
} finally {await browser.close();}
