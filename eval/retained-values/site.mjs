import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {launchBrowser, checkForm} from '../hosted-reserve/browser.mjs';

const here = fileURLToPath(new URL('./', import.meta.url));
const [name, stage] = process.argv.slice(2);
if (!['studio-pickup','caption-export'].includes(name) || !/^(before|after)(-\d+)?$/.test(stage)) {
  throw new Error('node site.mjs studio-pickup|caption-export before|after');
}
await mkdir(join(here,'runs'), {recursive:true});
const out = join(here,'runs',name+'-'+stage);
await mkdir(out);
const browser = await launchBrowser();
const receipt = {route:'site', started_utc:new Date().toISOString()};
try {
  const context = await browser.newContext({viewport:{width:390,height:844}});
  const page = await checkForm(context,'https://razbor-tz.onrender.com',receipt,out,join(here,name+'.md'));
  if (stage.startsWith('after')) {
    const link = page.locator('#run-difference');
    await link.waitFor({timeout:15000});
    receipt.shortcut = await link.innerText();
    const popup = page.waitForEvent('popup');
    await link.click();
    const report = await popup;
    const target = report.locator(':target');
    await target.waitFor();
    receipt.anchor = await target.getAttribute('id');
    receipt.displayed = await target.locator('.execution-comparison dd').allTextContents();
    if (receipt.displayed.length) {
      await target.locator('.execution-control summary').click();
      receipt.control = await target.locator('.execution-control').innerText();
    }
  }
  console.log(name, stage, receipt.status_text, receipt.displayed);
} catch(error) {
  receipt.error = error.message;
  throw error;
} finally {
  receipt.finished_utc = new Date().toISOString();
  delete receipt.job_id;
  await writeFile(join(out,'browser.json'), JSON.stringify(receipt,null,2)+'\n');
  await browser.close();
}
