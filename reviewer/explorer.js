/* Try different inputs on the saved programs. Only the server evaluates the rules. */
(() => {
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const format = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:0});
  const units = (value, unit) => typeof value === 'string' ? value : format.format(value) + (unit === '1' ? '' : ' ' + unit);
  const button = text => {
    const node = element('button', text);
    node.type = 'button';
    return node;
  };
  let opened = null;

  function resultCard(trace, program, states, caption) {
    const result = element('div', undefined, 'lab-result');
    result.append(element('p', caption, 'lab-origin'));
    const diff = trace.first_difference;
    const conclusion = diff ? 'Расхождение на шаге ' + diff.step : 'Совпадение на каждом шаге';
    result.append(element('h3', conclusion));
    if (diff) {
      const field = states.get(diff.field);
      result.append(element('p', field.label + ' · ' + units(diff.first, field.unit)
        + ' по правилу А и ' + units(diff.second, field.unit) + ' по правилу Б.'));
    }
    const table = element('table', undefined, 'execution-table');
    table.dataset.values = program.observe.some(key => states.get(key).unit.startsWith('enum:')) ? 'enum' : 'number';
    table.append(element('caption', 'Итог после последнего события'));
    const headings = table.createTHead().insertRow();
    for (const label of ['Показатель', 'Правило А', 'Правило Б']) {
      const cell = element('th', label);
      cell.scope = 'col';
      headings.append(cell);
    }
    const body = table.createTBody();
    for (const key of program.observe) {
      const state = states.get(key);
      const row = body.insertRow();
      const header = element('th', state.label);
      header.scope = 'row';
      row.append(header);
      for (const [side, label] of [['first','Правило А'],['second','Правило Б']]) {
        const cell = row.insertCell();
        cell.dataset.rule = label;
        cell.textContent = units(trace.final[side][key], state.unit);
      }
    }
    result.append(table);
    return result;
  }

  function scopeDetails(item, reportUrl) {
    const box = element('details', undefined, 'execution-sources');
    box.append(element('summary', 'Где применимы правила из отчёта'));
    for (const [side, letter] of [['first','А'],['second','Б']]) {
      const source = item.program[side].source;
      const quote = element('blockquote');
      const label = element('span', 'Правило ' + letter + ' · ');
      const link = element('a', 'строка ' + source.line + ' ↗');
      link.href = reportUrl + '#source-line-' + source.line;
      link.target = '_blank';
      link.rel = 'noopener';
      label.append(link);
      quote.append(label, document.createTextNode(source.quote));
      box.append(quote);
      if (item.audit?.[side + '_rule']) box.append(element('p', item.audit[side + '_rule'], 'execution-reading'));
    }
    return box;
  }

  function openExplorer(item, endpoint, reportUrl, trigger) {
    if (opened) opened.close();
    const program = item.program;
    const states = new Map(program.states.map(field => [field.key, field]));
    const inputs = new Map(program.inputs.map(field => [field.key, field]));
    const domains = new Map((program.domains ?? []).map(d => ['enum:' + d.key, d.options]));
    const dialog = element('dialog', undefined, 'execution lab-dialog');
    const title = element('h2', 'Расчёт на своих данных');
    title.id = 'lab-title';
    title.tabIndex = -1;
    dialog.setAttribute('aria-labelledby', title.id);
    const close = button('Закрыть');
    close.className = 'lab-close';
    close.addEventListener('click', () => dialog.close());
    const header = element('div', undefined, 'lab-head');
    header.append(title, close);
    const description = element('p', 'Меняйте входные данные для тех же событий. Результат показывает, как поведут себя две модели из отчёта ' + item.finding_id + '.');
    const presets = element('div', undefined, 'lab-presets');
    presets.setAttribute('role', 'group');
    presets.setAttribute('aria-label', 'Набор входных значений');
    const form = element('form', undefined, 'lab-form');
    const fields = element('div', undefined, 'lab-fields');
    const submit = element('button', 'Пересчитать');
    submit.type = 'submit';
    submit.className = 'lab-submit';
    const output = element('div', undefined, 'lab-output');
    output.setAttribute('aria-live', 'polite');
    output.setAttribute('aria-atomic', 'true');
    form.append(fields, submit);
    dialog.append(header, description, presets, output, form);
    const presetButtons = new Map();
    let draft, pending;

    function cancel() {
      pending?.abort();
      pending = null;
      submit.disabled = false;
      output.removeAttribute('aria-busy');
    }
    function showFields(title, values, definitions, sourceEvent) {
      const fieldset = element('fieldset');
      fieldset.append(element('legend', title));
      if (sourceEvent) {
        const source = element('details', undefined, 'lab-event');
        source.append(element('summary', 'Событие в сохранённом примере'), element('p', sourceEvent));
        fieldset.append(source);
      }
      for (const entry of values) {
        const field = definitions.get(entry.key);
        const options = domains.get(field.unit);
        const label = element('label', field.label + (options || field.unit === '1' ? '' : ' · ' + field.unit));
        const input = element(options ? 'select' : 'input');
        const citation = options ? element('a', undefined, 'lab-value-source') : null;
        function showCitation() {
          const source = options.find(option => option.value === input.value).source;
          citation.href = reportUrl + '#source-line-' + source.line;
          citation.textContent = 'Значение из строки ' + source.line + ' ↗';
        }
        if (options) {
          for (const option of options) input.append(new Option(option.value, option.value));
          citation.target = '_blank';
          citation.rel = 'noopener';
        } else {
          input.type = 'number';
          input.min = String(Number.MIN_SAFE_INTEGER);
          input.max = String(Number.MAX_SAFE_INTEGER);
          input.step = '1';
        }
        input.required = true;
        input.value = String(entry.value);
        input.addEventListener(options ? 'change' : 'input', () => {
          cancel();
          entry.value = options ? input.value : input.valueAsNumber;
          if (options) showCitation();
          for (const b of presetButtons.values()) b.setAttribute('aria-pressed', 'false');
          output.textContent = 'Значения изменены. Пересчитайте исходы.';
        });
        label.append(input);
        if (options) {
          showCitation();
          const group = element('div', undefined, 'lab-enum-field');
          group.append(label, citation);
          fieldset.append(group);
        } else fieldset.append(label);
      }
      fields.append(fieldset);
    }
    function select(name) {
      cancel();
      draft = structuredClone(program[name]);
      fields.replaceChildren();
      showFields('До первого события', draft.initial, states);
      draft.events.forEach((event, i) => {
        if (event.values.length) showFields('Входные данные шага ' + (i + 1), event.values, inputs, event.label);
      });
      for (const [key, control] of presetButtons) control.setAttribute('aria-pressed', String(key === name));
      output.replaceChildren(resultCard(item.result[name], program, states,
        name === 'scenario' ? 'Пример из отчёта' : 'Контроль из отчёта'));
    }
    for (const [key, label] of [['scenario','Исходный пример'],['control','Контроль']]) {
      const control = button(label);
      control.addEventListener('click', () => select(key));
      presetButtons.set(key, control);
      presets.append(control);
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      // Number inputs can still parse a scientific notation mantissa with lost precision.
      for (const input of fields.querySelectorAll('input')) {
        if (!/^-?\d+$/.test(input.value) || !Number.isSafeInteger(input.valueAsNumber)) {
          output.textContent = 'Введите целое число без дробной части и экспоненты.';
          input.focus();
          return;
        }
      }
      cancel();
      const request = new AbortController();
      pending = request;
      submit.disabled = true;
      output.setAttribute('aria-busy','true');
      output.textContent = 'Считаем оба исхода…';
      try {
        const res = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({case:draft}), cache:'no-store', signal:request.signal});
        const body = await res.json();
        if (pending !== request) return;
        if (!res.ok) throw new Error(body.error || 'Пересчитайте значения ещё раз.');
        const card = resultCard(body.trace, program, states, 'Расчёт для введённых значений');
        const note = body.trace.first_difference ? 'Обе модели посчитали эти входы по сохранённым формулам.'
          : 'Совпадение относится только к введённым значениям и сохранённым формулам.';
        card.append(element('p', note + ' Правило приёмки для изменённых условий подтверждает владелец ТЗ.', 'lab-scope'));
        if (body.explanation_html) {
          const template = document.createElement('template');
          // The same-origin route renders escaped data after replaying the validated report.
          template.innerHTML = body.explanation_html;
          for (const link of template.content.querySelectorAll('a[href^="#source-line-"]')) {
            link.setAttribute('href', reportUrl + link.getAttribute('href'));
            link.target = '_blank';
            link.rel = 'noopener';
          }
          card.append(template.content);
        }
        card.append(scopeDetails(item, reportUrl));
        output.replaceChildren(card);
        output.scrollIntoView({block:'nearest'});
      } catch (error) {
        if (pending === request) output.textContent = 'Расчёт недоступен. ' + error.message;
      } finally {
        if (pending === request) {
          pending = null;
          submit.disabled = false;
          output.removeAttribute('aria-busy');
        }
      }
    });
    dialog.addEventListener('close', () => {
      cancel();
      dialog.remove();
      if (opened === dialog) {
        opened = null;
        if (trigger.isConnected) trigger.focus({preventScroll:true});
      }
    }, {once:true});
    document.body.append(dialog);
    opened = dialog;
    select('scenario');
    dialog.showModal();
    title.focus({preventScroll:true});
  }

  window.addEventListener('review:loaded', () => opened?.close());
  window.addEventListener('review:execution-panel', ({detail}) => {
    const {panel, item, endpoint, reportUrl} = detail;
    if (item.status !== 'executed') return;
    const edit = button('Изменить входные данные');
    edit.className = 'execution-edit';
    edit.addEventListener('click', () => openExplorer(item, endpoint, reportUrl, edit));
    panel.insertBefore(edit, panel.querySelector('details'));
  });
})();
