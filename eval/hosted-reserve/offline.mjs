// Browser harness rehearsal. A loopback server deliberately returns the wrong archived report.
// No project endpoint, provider credentials or model calls are used.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {checkForm, here, launchBrowser, root} from './browser.mjs';

const browser = await launchBrowser();
try {
  for (const route of ['site', 'local']) {
    const fixture = await readFile(join(here, 'bindery-' + route + '.md'));
    const endpoint = route === 'local' ? '/api/project-runs' : '/api/runs';
    const job = randomUUID().replaceAll('-', '');
    let uploads = 0;
    const server = createServer(async (req, res) => {
      function send(code, type, data) { res.writeHead(code, {'Content-Type':type}); res.end(data); }
      const path = new URL(req.url, 'http://localhost').pathname;
      if (req.method === 'POST' && path === endpoint) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        if (!Buffer.concat(chunks).equals(fixture)) return send(400, 'text/plain', 'Wrong upload');
        uploads++;
        return send(202, 'application/json', JSON.stringify({id:job}));
      }
      if (path === '/') {
        let markup = await readFile(join(root, 'reviewer/workbench.html'), 'utf8');
        if (route === 'site') markup = markup.replace(/<!--project-button-->.*?<!--\/project-button-->/s, '');
        return send(200, 'text/html; charset=utf-8', markup);
      }
      if (path === '/api/status/' + job) return send(200, 'application/json',
        JSON.stringify({state:'done', mode:'full', progress:[], ...(route === 'local' ? {execution:'project'} : {})}));
      if (['.html','.json'].some(ext => path === '/reports/' + job + '/review' + ext)) {
        const ext = path.split('.').at(-1);
        return send(200, ext === 'html' ? 'text/html' : 'application/json',
          await readFile(join(root, 'eval/jury-fallback/runs/astra-v1.' + ext)));
      }
      if (/^\/reviewer\/(assistant|execution|explorer|guided)\.(js|css)$/.test(path)) {
        return send(200, path.endsWith('css') ? 'text/css' : 'text/javascript', await readFile(join(root, path)));
      }
      send(404, 'text/plain', 'No prepared response');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    const folder = await mkdtemp(join(tmpdir(), 'bindery-rehearsal-'));
    const context = await browser.newContext({serviceWorkers:'block'});
    try {
      await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort());
      const receipt = {route, started_utc:new Date().toISOString()};
      await checkForm(context, origin, receipt, folder);
      assert.equal(uploads, 1);
      await writeFile(join(folder, 'browser.json'), JSON.stringify(receipt));
      const audit = spawnSync(process.env.PYTHON || 'python3', ['-m', 'eval.hosted-reserve.verify', route, folder],
        {cwd:root, encoding:'utf8'});
      assert.equal(audit.status, 1, audit.stderr);
      assert.match(JSON.parse(audit.stdout).error, /Имя загруженного файла отличается/);
      console.log(route + ': браузер отправил исходные байты; подставленный отчёт отклонён.');
    } finally {
      await context.close();
      await new Promise(done => server.close(done));
      await rm(folder, {recursive:true, force:true});
    }
  }
} finally {
  await browser.close();
}
