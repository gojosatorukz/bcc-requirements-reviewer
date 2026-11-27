import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
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
