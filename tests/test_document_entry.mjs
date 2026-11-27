import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../reviewer/workbench.html', import.meta.url), 'utf8');
const activeDocument = html.slice(html.indexOf('function activeDocument()'), html.indexOf('function syncInputSource()'));

test('the active source alone supplies the review body while both drafts survive switching', async () => {
  const markdown = '  # Ереже 🇰🇿\n\n| Операция | Сома |\n| --- | --- |\n| Төлем | 4 000 ₸ |\n';
  const input = vm.createContext({
    File,
    form:{elements:{source:{value:'text'}}},
    pastedText:{value:markdown},
    uploadedFile:new File(['another document'], 'different.txt'),
  });
  vm.runInContext(activeDocument, input);
  const pasted = input.activeDocument();
  assert.match(pasted.name, /\.md$/);
  assert.deepEqual(Buffer.from(await pasted.arrayBuffer()), Buffer.from(markdown, 'utf8'));

  input.form.elements.source.value = 'file';
  assert.equal(input.activeDocument(), input.uploadedFile);
  input.form.elements.source.value = 'text';
  assert.equal(await input.activeDocument().text(), markdown);
  input.pastedText.value = ' \n\t ';
  assert.equal(input.activeDocument(), null);
  input.form.elements.source.value = 'file';
  assert.equal(input.activeDocument(), input.uploadedFile);
});

const base = process.env.REVIEWER_WORKBENCH_URL;

test('a jury visitor can read the saved example, keep their text after a quota error and open their own JSON', {skip:!base}, async () => {
  const require = createRequire(import.meta.url);
  const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
  try {
    const page = await browser.newPage({viewport:{width:320,height:740},reducedMotion:'reduce'});
    const jobs = [];
    // Exercise both server failure paths without submitting a document to a model.
    await page.route('**/api/runs?*', async route => {
      jobs.push(route.request().postData());
      if (jobs.length === 1) return route.fulfill({status:202,json:{id:'a'.repeat(32)}});
      return route.fulfill({status:429,json:{error:'Агент занят. Попробуйте позже.'}});
    });
    await page.route('**/api/status/' + 'a'.repeat(32), route => route.fulfill({json:{
      state:'failed', mode:'full', error:'Достигнут лимит запросов к модели.',
    }}));
    await page.goto(base);
    const report = page.locator('#saved-report-link');
    await report.waitFor();
    const bounds = await report.boundingBox();
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 740);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const popupPromise = page.waitForEvent('popup');
    await report.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    assert.equal(new URL(popup.url()).pathname, '/reports/demo/review.html');
    assert.match(await popup.locator('body').innerText(), /Уведомление о расходах по карте/);
    await popup.close();
    assert.equal(jobs.length, 0);

    const draft = '  Реквизиты заказа\nКомиссия банка — 0 ₸.\n';
    await page.locator('.nav-start').click();
    const invalidJson = ['# ТЗ', '| Поле |', '| --- |', '| id |', '```json', '{"id":}', '```'].join('\n');
    await page.locator('#pasted-text').fill(invalidJson);
    async function inspect(button) {
      const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/structure');
      await page.locator(button).click();
      const reply = await response;
      assert.equal(reply.status(), 200, await reply.text());
    }
    await inspect('#structure-check');
    await page.locator('#structure-diagnostics li').filter({hasText:'Строка 6 · Ожидалось значение JSON.'}).waitFor();
    assert.deepEqual(await page.locator('#structure-counts dd').allTextContents(), ['7', '4', '1', '1']);
    assert.equal(jobs.length, 0);
    await page.locator('#pasted-text').fill(draft);
    assert.equal(await page.locator('#structure-result').isVisible(), false);
    for (const message of ['Достигнут лимит', 'Агент занят']) {
      await page.locator('#submit').click();
      await page.locator('#status-message').filter({hasText:message}).waitFor();
      assert.equal(await page.locator('#run-recovery').isVisible(), true);
    }
    assert.deepEqual(jobs, [draft, draft]);
    await inspect('#recovery-structure');
    await page.locator('#structure-summary').filter({hasText:'не распознано примеров JSON'}).waitFor();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download-source').click();
    const download = await downloadPromise;
    assert.equal(readFileSync(await download.path(), 'utf8'), draft);
    await page.locator('#run-recovery a[href="#local-review"]').click();
    assert.equal(await page.locator('#local-review').evaluate(el => el.open), true);
    assert.match(await page.locator('#local-review code').innerText(), /python -m reviewer /);
    await page.locator('#local-review a[href="#restore-review"]').click();
    assert.equal(await page.locator('#restore-review').evaluate(el => el.open), true);

    const other = fileURLToPath(new URL('../eval/executable/runs/named-state.json', import.meta.url));
    await page.locator('#import-file').setInputFiles(other);
    await page.locator('#review-origin').filter({hasText:'Из JSON'}).waitFor();
    const active = await page.locator('#active-report-link').getAttribute('href');
    assert.match(active, /^\/reports\/[a-f0-9]{32}\/review\.html$/);
    const exportLink = page.getByRole('link', {name:'Скачать задачи для Jira (CSV)'});
    assert.ok((await exportLink.getAttribute('href')).startsWith(active.replace('review.html', 'issues.csv?sha256=')));
    const csvPromise = page.waitForEvent('download');
    await exportLink.click();
    const csv = await csvPromise;
    assert.equal(csv.suggestedFilename(), 'review-issues.csv');
    const body = readFileSync(await csv.path(), 'utf8');
    assert.match(body, /Summary,Issue Type,Priority,Description/);
    assert.ok(body.includes(JSON.parse(readFileSync(other, 'utf8')).run.input_sha256));
    assert.match(await report.getAttribute('href'), /^\/reports\/demo\/review\.html\?sha256=[a-f0-9]{64}$/);
    await page.getByRole('link', {name:'К разбору своего ТЗ'}).click();
    assert.equal(await page.locator('#active-report-link').getAttribute('href'), active);
  } finally {
    await browser.close();
  }
});
