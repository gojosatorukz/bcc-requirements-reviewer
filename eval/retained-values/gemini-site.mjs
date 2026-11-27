import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {launchBrowser} from '../hosted-reserve/browser.mjs';

const home = 'https://razbor-tz.onrender.com';
const fixture = 'Очередь на просмотр слайдов\nПри первом просмотре ведущий видит только титульный слайд.\nПриёмка требует показать при первом просмотре сразу все три слайда.\n';
const browser = await launchBrowser();
try {
  const page = await browser.newPage({viewport:{width:390, height:844}});
  await page.goto(home, {timeout:120000});
  const model = await page.locator('#upload-form .notice strong').innerText();
  assert.equal(model, 'gemini-3.5-flash-lite');
  await page.getByText('Вставить текст', {exact:true}).click();
  await page.locator('#pasted-text').fill(fixture);
  const submitted = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/runs' && r.request().method()==='POST');
  await page.locator('#submit').click();
  const response = await submitted;
  assert.equal(response.status(), 202);
  await page.waitForFunction(()=>!document.querySelector('#run-links').hidden || !document.querySelector('#run-recovery').hidden, null, {timeout:15*60000});
  const result = {origin:home,model,checked_utc:new Date().toISOString(),input:fixture,submitted:response.status(),
    status:await page.locator('#status-message').innerText()};
  if (await page.locator('#run-links').isVisible()) {
    const link = await page.locator('#run-json').getAttribute('href');
    const report = await (await page.request.get(new URL(link, home).href)).json();
    assert.equal(report.run.provider, 'gemini-api');
    result.full_review = {model:report.run.model, findings:report.whole.findings.length};
  } else {
    assert.match(result.status,/квот|лимит/i);
    assert.ok(!result.status.toLowerCase().includes('резерв'));
    await page.locator('#recovery-structure').click();
    await page.locator('#structure-counts').waitFor({state:'visible'});
    result.structure = await page.locator('#structure-counts').innerText();
  }
  await writeFile(new URL('./runs/gemini-site.json', import.meta.url), JSON.stringify(result,null,2)+'\n');
  console.log(result);
} finally {await browser.close();}
