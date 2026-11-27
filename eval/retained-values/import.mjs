import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {launchBrowser} from '../hosted-reserve/browser.mjs';

const home = 'https://razbor-tz.onrender.com';
const dir = fileURLToPath(new URL('./runs/',import.meta.url));
const browser = await launchBrowser();
try {
  for (const name of ['studio-pickup','caption-export']) {
    const folder = join(dir,name+'-assisted');
    const context = await browser.newContext({viewport:{width:390,height:844}});
    const page = await context.newPage();
    await page.goto(home,{timeout:120000});
    await page.locator('#restore-review summary').click();
    const response = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/import-report' && r.request().method()==='POST');
    await page.locator('#import-file').setInputFiles(join(folder,'review.json'));
    const reply = await response;
    assert.ok(reply.ok(),await reply.text());
    await page.locator('#review-origin').filter({hasText:'Из JSON'}).waitFor();
    const open = page.waitForEvent('popup');
    await page.locator('#active-difference').click();
    const popup = await open;
    const target = popup.locator(':target');
    await target.waitFor();
    const displayed = await target.locator('.execution-comparison dd').allTextContents();
    assert.equal(displayed.length,2);
    await target.locator('.execution-control summary').click();
    const control = await target.locator('.execution-control').innerText();
    const modelNote = await target.locator('.execution-caption').allTextContents();
    assert.ok(modelNote.some(note=>note.includes('gpt-6.2-astra')&&note.includes('gpt-oss-120b')));
    const receipt = {route:'import',origin:home,status:reply.status(),checked_utc:new Date().toISOString(),
      anchor:await target.getAttribute('id'),import_sha256:(await reply.json()).sha256,displayed,control,modelNote};
    await writeFile(join(folder,'browser.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(name,displayed);
    await context.close();
  }
} finally {await browser.close();}
