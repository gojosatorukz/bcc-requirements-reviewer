import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

const base = process.env.REVIEWER_WORKBENCH_URL;
const require = createRequire(import.meta.url);
const release = fileURLToPath(new URL('../runs/bcc-jury/review.json', import.meta.url));
const other = fileURLToPath(new URL('../eval/executable/runs/named-state.json', import.meta.url));

// Integration test against a local workbench. Only the assistant reply is stubbed.
test('a late answer stays with its report and expired continuations preserve the draft', {skip:!base}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
  try {
    const page = await browser.newPage({viewport:{width:412,height:820}, reducedMotion:'reduce'});
    await page.goto(base);
    const questions = [];
    let finish;
    const received = new Promise(resolve => { finish = resolve; });
    let releaseFirst;
    const pending = new Promise(resolve => { releaseFirst = resolve; });
    await page.route('**/api/assistant/**', async route => {
      const payload = route.request().postDataJSON();
      questions.push(payload);
      const hash = new URL(route.request().url()).pathname.split('/').at(-1);
      if (questions.length === 1) { finish(); await pending; }
      if (questions.length === 2) {
        return route.fulfill({status:410, json:{error:'Срок диалога истёк.'}});
      }
      return route.fulfill({status:200, json:{token:'b'.repeat(32), sha256:hash,
        answer:'Сверьте пункт с владельцем.', sources:[]}});
    });
    async function openFile(file) {
      const sha = createHash('sha256').update(await readFile(file)).digest('hex');
      await page.locator('#import-file').setInputFiles(file);
      await page.waitForFunction(hash=>document.querySelector('#active-qa').dataset.report?.endsWith(hash),sha);
      await page.locator('#assistant-open').click();
      return sha;
    }
    const draft = 'Что уточнить у владельца?';
    await openFile(release);
    await page.locator('#assistant-input').fill(draft);
    await page.locator('#assistant-send').click();
    await received;
    await page.keyboard.press('Escape');
    await openFile(other);
    releaseFirst();
    await page.waitForFunction(()=>Object.keys(sessionStorage).some(key=>key.startsWith('reviewer.assistant.')
      && JSON.parse(sessionStorage[key]).turns.length));
    assert.equal(await page.locator('#assistant-log .assistant-turn').count(),0);
    await page.keyboard.press('Escape');
    await openFile(release);
    assert.equal(await page.locator('.assistant-answer-text').innerText(),'Сверьте пункт с владельцем.');
    await page.locator('#assistant-input').fill('Какие строки подтвердят ответ?');
    await page.locator('#assistant-send').click();
    await page.locator('#assistant-restart').waitFor();
    assert.match(await page.locator('#assistant-status').innerText(),/истёк/);
    assert.equal(await page.locator('#assistant-input').inputValue(),'Какие строки подтвердят ответ?');
    await page.locator('#assistant-restart').click();
    await page.waitForFunction(()=>document.querySelectorAll('.assistant-answer-text').length===2);
    assert.deepEqual(questions.map(q=>q.previous),[null,'b'.repeat(32),null]);
    assert.equal(await page.locator('#assistant-panel').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
    assert.equal(await page.locator('#assistant-panel').evaluate(el=>getComputedStyle(el).animationName),'none');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#assistant-open').evaluate(el=>document.activeElement===el),true);
  } finally { await browser.close(); }
});

test('the recheck action discusses the revision while the header retains the full review', {skip:!base}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
  try {
    const page = await browser.newPage();
    const id = 'c'.repeat(32);
    const file = new URL('../runs/bcc-jury/owner/wrong-revision.json', import.meta.url);
    const bytes = await readFile(file);
    const sha = createHash('sha256').update(bytes).digest('hex');
    const report = JSON.parse(bytes);
    await page.addInitScript(({id,name}) => {
      window.addEventListener('review:loaded',e=>{ window.fullReviewForTest=e.detail; });
      sessionStorage.setItem('reviewer.workbench.v1',JSON.stringify({last:{id,mode:'recheck',name},full:[]}));
    },{id,name:report.run.input_name});
    await page.route('**/api/status/'+id,route=>route.fulfill({json:{state:'done',mode:'recheck',progress:[]}}));
    await page.route('**/reports/'+id+'/review.json',route=>route.fulfill({
      body:bytes,headers:{'Content-Type':'application/json','X-Report-SHA256':sha},
    }));
    await page.route('**/api/assistant/'+id+'/'+sha,route=>{
      assert.equal(route.request().postDataJSON().focus,report.checks[0].id);
      return route.fulfill({json:{answer:'Цитата из новой редакции.',token:'c'.repeat(32),sha256:sha,
        sources:[{label:'Строка 38',excerpt:'Покупки',href:'/reports/'+id+'/review.html?sha256='+sha+'#line-38'}]}});
    });
    await page.goto(base);
    await page.locator('#assistant-recheck-open').click();
    assert.match(await page.locator('#assistant-document').innerText(),/Перепроверка/);
    await page.waitForFunction(()=>window.fullReviewForTest);
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('review:loaded',{detail:window.fullReviewForTest})));
    assert.match(await page.locator('#assistant-document').innerText(),/Перепроверка/);
    await page.locator('#assistant-focus').selectOption(report.checks[0].id);
    await page.locator('#assistant-input').fill('Что изменилось?');
    await page.locator('#assistant-send').click();
    const link = page.locator('.assistant-citations a');
    await link.waitFor();
    assert.equal(await link.getAttribute('href'),'/reports/'+id+'/review.html?sha256='+sha+'#line-38');
    await page.keyboard.press('Escape');
    await page.locator('#assistant-open').click();
    assert.match(await page.locator('#assistant-document').innerText(),/Полное ревью/);
    assert.equal(await page.locator('#assistant-log .assistant-turn').count(),0);
    await page.keyboard.press('Escape');
    await page.locator('#assistant-recheck-open').click();
    assert.equal(await page.locator('#assistant-log .assistant-answer-text').innerText(),'Цитата из новой редакции.');
  } finally { await browser.close(); }
});

test('the companion restores the actual elapsed clock and keeps questions on the open report', {skip:!base}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_BINARY});
  try {
    const page = await browser.newPage({viewport:{width:320,height:740}, reducedMotion:'reduce'});
    const id = 'f'.repeat(32);
    await page.addInitScript(id => {
      sessionStorage.setItem('reviewer.workbench.v1', JSON.stringify({
        last:{id,mode:'full',name:'Свой документ.md',submittedAt:Date.now()-125000},full:[]
      }));
    }, id);
    let state = 'running';
    let phase = 'whole';
    await page.route('**/api/status/'+id, route => route.fulfill({json:{
      state,mode:'full',progress:[],coverage:{phase,checked:1,total:1}
    }}));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#companion-stage').textContent === 'Сверка требований'
      && document.querySelector('#companion-context').textContent === 'Демо-отчёт');
    assert.match(await page.locator('#run-clock').innerText(), /С отправки документа · 2:\d{2}/);
    assert.equal(await page.locator('#brand-face').getAttribute('data-state'), 'working');
    assert.equal(await page.locator('#assistant-open .brand-eyes').evaluate(el=>getComputedStyle(el).animationName),'none');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    await page.locator('#assistant-open').focus();
    await page.keyboard.press('Enter');
    assert.match(await page.locator('#assistant-document').innerText(), /Полное ревью/);
    assert.match(await page.locator('#assistant-run-note').innerText(), /Свой документ\.md · Сверка требований/);
    assert.equal(await page.locator('#run-face').getAttribute('data-phase'), 'whole');
    for (const next of ['scenarios', 'executables', 'render']) {
      phase = next;
      await page.waitForFunction(expected => document.querySelector('#brand-face').dataset.phase === expected, next);
      assert.equal(await page.locator('#run-face').getAttribute('data-phase'), next);
    }
    state = 'failed';
    await page.waitForFunction(()=>document.querySelector('#brand-face').dataset.state === 'failed');
    assert.equal(await page.locator('#assistant-run-note').isVisible(), false);
    assert.equal(await page.locator('#run-clock').isVisible(), false);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#assistant-open').evaluate(el=>document.activeElement===el),true);
    assert.equal(await page.locator('#companion-context').innerText(), 'Демо-отчёт');
  } finally { await browser.close(); }
});
