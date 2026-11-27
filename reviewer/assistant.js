(() => {
  'use strict';
  const element = id => document.getElementById(id);
  const panel = element('assistant-panel');
  const shell = panel.querySelector('.assistant-shell');
  const scope = element('assistant-focus');
  const input = element('assistant-input');
  const form = element('assistant-form');
  const log = element('assistant-log');
  const empty = element('assistant-empty');
  const scroll = element('assistant-scroll');
  const status = element('assistant-status');
  const submit = element('assistant-send');
  const restart = element('assistant-restart');
  const documents = new Map();
  const storagePrefix = 'reviewer.assistant.v1:';
  const axes = {initial_action:'Первое действие',retry_effect:'Повтор действия',
    reentry:'Повторное наступление условия',boundary:'Граница значения',isolation:'Независимость объектов'};
  let active = null;
  let opener;
  let currentFull;
  let currentRecheck;

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text) result.textContent = text;
    return result;
  }

  function restore(key) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storagePrefix + key));
      if (saved && Array.isArray(saved.turns)) return saved;
    } catch { /* Histories remain available in this tab when browser storage is unavailable. */ }
    return {turns:[], draft:'', focus:'', token:null};
  }

  function save(view) {
    try { sessionStorage.setItem(storagePrefix + view.key, JSON.stringify(view.history)); }
    catch { /* The live document keeps the in-memory transcript. */ }
  }

  function sourceLink(view, source) {
    if (!source || typeof source.href !== 'string' || typeof source.label !== 'string') return null;
    let url;
    try { url = new URL(source.href, location.origin + view.reportUrl); }
    catch { return null; }
    if (url.origin !== location.origin || url.pathname !== view.reportUrl || !url.hash
        || (url.searchParams.has('sha256') && url.searchParams.get('sha256') !== view.sha)) return null;
    const anchor = url.hash.slice(1);
    if (!view.anchors.has(anchor)) return null;
    url.search = new URLSearchParams({sha256:view.sha}).toString();
    const link = node('a', '', source.label);
    link.href = url.pathname + url.search + url.hash;
    link.target = '_blank';
    link.rel = 'noopener';
    if (typeof source.excerpt === 'string' && source.excerpt) link.title = source.excerpt;
    return link;
  }

  function showTurn(view, turn) {
    const block = node('div', 'assistant-turn');
    const question = node('p', 'assistant-question', turn.question);
    question.prepend(node('span','assistant-sr','Вы. '));
    block.append(question);
    if (turn.answer) {
      const answer = node('div','assistant-answer');
      answer.append(node('span','assistant-sr','Помощница. '),node('p','assistant-answer-text',turn.answer));
      const references = (turn.sources || []).map(source => sourceLink(view, source)).filter(Boolean);
      if (references.length) {
        const links = node('div','assistant-citations');
        links.setAttribute('role','group');
        links.setAttribute('aria-label','Основания ответа в отчёте');
        links.append(...references);
        answer.append(links);
        const quotes = (turn.sources || []).filter(source => source.excerpt && sourceLink(view, source));
        if (quotes.length) {
          const details = node('details','assistant-quotes');
          details.append(node('summary','','Прочитать цитаты'));
          for (const source of quotes) {
            const quote = node('blockquote','',source.excerpt);
            quote.prepend(node('strong','',source.label+' '));
            details.append(quote);
          }
          answer.append(details);
        }
      }
      block.append(answer);
    }
    return block;
  }

  function syncComposer() {
    const busy = Boolean(active?.pending);
    input.disabled = !active;
    input.readOnly = busy;
    scope.disabled = busy;
    submit.disabled = !active || busy || active.expired || !input.value.trim();
    shell.toggleAttribute('data-busy', busy);
    status.toggleAttribute('data-error', Boolean(active?.error));
    const waiting = busy && Date.now() - active.started > 18000;
    const progress = waiting ? 'Ответ модели ещё не пришёл. Панель можно закрыть.' : 'Готовим ответ по отчёту…';
    status.textContent = active?.error || (busy ? progress : (active?.announcement || ''));
    restart.hidden = !active?.expired || busy;
    restart.disabled = !input.value.trim();
  }

  function showConversation(toEnd) {
    log.replaceChildren();
    empty.hidden = Boolean(active && (active.history.turns.length || active.pending));
    if (active) {
      for (const turn of active.history.turns) log.append(showTurn(active, turn));
      if (active.pending) log.append(showTurn(active, {question:active.pending}));
      if (!empty.hidden) {
        const ideas = node('div','assistant-ideas');
        const first = active.findings[0];
        const prompts = [active.isRecheck ? 'Какие замечания остались открыты?' : 'Какие проверки уже можно подготовить?'];
        if (first) prompts.unshift('Объясни замечание ' + first.id);
        for (const prompt of prompts) {
          const button = node('button','assistant-idea',prompt);
          button.type = 'button';
          button.addEventListener('click', () => { input.value=prompt; storeDraft(); input.focus(); });
          ideas.append(button);
        }
        const glyph = node('span','chat-glyph');
        glyph.setAttribute('aria-hidden','true');
        empty.replaceChildren(glyph,node('p','','Что разобрать в этом отчёте?'),ideas);
      }
    }
    input.value = active?.pending ? '' : (active?.history.draft || '');
    syncComposer();
    if (toEnd && panel.open) scroll.scrollTop = scroll.scrollHeight;
  }

  function renderScope(view) {
    scope.replaceChildren(new Option('Весь отчёт',''));
    const groups = [
      ['Замечания',view.findings.map(f => [f.id, f.id + ' · ' + f.title])],
      ['Сценарии для QA',(view.report.lifecycle_checks?.checks || []).map((c,i) =>
        ['qa-scenario-' + (i+1), axes[c.axis] || c.axis])],
    ];
    for (const [label,entries] of groups) {
      if (!entries.length) continue;
      const group = document.createElement('optgroup');
      group.label=label;
      for (const [value,title] of entries) group.append(new Option(title,value));
      scope.append(group);
    }
    scope.value = view.history.focus || '';
    if (scope.selectedIndex < 0) scope.value = '';
  }

  function bind({report, key, reportUrl}) {
    const match = /^(demo|[a-f0-9]{32}):([a-f0-9]{64})$/.exec(key || '');
    const isRecheck = report?.report_type === 'recheck';
    const findings = isRecheck ? report.checks : report?.whole?.findings;
    if (!match || !Array.isArray(findings) || reportUrl !== '/reports/' + match[1] + '/review.html') {
      active = null;
      element('assistant-document').textContent = 'Документ ещё не открыт';
      element('assistant-scope').hidden = true;
      empty.replaceChildren(node('p','','Откройте полный отчёт, чтобы задать вопрос.'));
      showConversation(false);
      return;
    }
    const [,id,sha] = match;
    if (active?.key === key) return;
    if (!documents.has(key)) {
      const checks = report.lifecycle_checks?.checks || [];
      const anchors = new Set(['report','actions','quality','register','coverage','source','passport',
        'response-fields','qa-scenarios','json-syntax',...findings.map(f=>f.id),
        ...(report.registry || []).map(r=>r.id),...report.source.map(s=>(isRecheck ? 'line-' : 'source-line-')+s.line),
        ...checks.map((_,i)=>'qa-scenario-'+(i+1)),
        ...(report.executables?.items || []).map(item=>'execution-'+item.finding_id)]);
      documents.set(key,{key,id,sha,reportUrl,report,findings,isRecheck,anchors,history:restore(key),pending:'',error:'',expired:false,announcement:''});
    }
    active = documents.get(key);
    element('assistant-document').textContent = (isRecheck ? 'Перепроверка · ' : 'Полное ревью · ') + report.run.input_name;
    element('assistant-scope').hidden = false;
    renderScope(active);
    showConversation(true);
  }

  function openPanel(source, focus) {
    opener = source;
    if (active && focus && !active.pending) {
      scope.value = focus;
      if (scope.selectedIndex >= 0) {
        active.history.focus = focus;
        save(active);
      } else scope.value = '';
    }
    if (!panel.open) panel.showModal();
    if (active && !active.pending) input.focus({preventScroll:true});
    else element('assistant-close').focus({preventScroll:true});
    scroll.scrollTop = scroll.scrollHeight;
  }

  function storeDraft() {
    if (!active || active.pending) return;
    active.history.draft = input.value;
    active.announcement = '';
    save(active);
    syncComposer();
  }

  async function ask(startOver = false) {
    const view = active;
    const question = input.value.trim();
    if (!view || view.pending || !question || question.length > 1600) return;
    view.history.draft = question;
    const previous = startOver ? null : (view.history.token || null);
    const focus = scope.value;
    view.pending = question;
    view.started = Date.now();
    const slow = setTimeout(() => { if (active === view && view.pending) syncComposer(); }, 19000);
    view.error = '';
    view.expired = false;
    view.announcement = '';
    save(view);
    showConversation(true);
    try {
      const response = await fetch('/api/assistant/'+view.id+'/'+view.sha, {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question,focus:focus || null,previous}),
      });
      const result = await response.json().catch(()=>({}));
      if (!response.ok) {
        view.expired = response.status === 410;
        throw new Error(result.error || 'Ответ не получен. Попробуйте ещё раз.');
      }
      if (result.sha256 !== view.sha || typeof result.answer !== 'string' || !result.answer.trim()
          || typeof result.token !== 'string' || !result.token || !Array.isArray(result.sources)
          || result.sources.some(source => !sourceLink(view, source))) {
        throw new Error('Не удалось подтвердить ответ для открытой версии отчёта.');
      }
      view.history.turns.push({question,answer:result.answer,sources:result.sources});
      view.history.token = result.token;
      view.history.draft = '';
      view.announcement = 'Ответ получен.';
    } catch (error) {
      view.error = error instanceof TypeError ? 'Нет связи с локальным приложением. Вопрос сохранён.' : error.message;
    } finally {
      clearTimeout(slow);
      view.pending = '';
      save(view);
      if (view === active) {
        showConversation(true);
        if (panel.open && !view.error && view.announcement) {
          status.append(node('span','assistant-sr',' ' + view.history.turns.at(-1).answer));
        }
      }
    }
  }

  element('assistant-open').addEventListener('click', event => {
    if (currentFull) bind(currentFull);
    openPanel(event.currentTarget);
  });
  element('assistant-recheck-open').addEventListener('click', event => {
    if (currentRecheck) { bind(currentRecheck); openPanel(event.currentTarget); }
  });
  element('assistant-qa-open').addEventListener('click', event => {
    if (currentFull) bind(currentFull);
    const index = element('qa-select').value;
    openPanel(event.currentTarget, index && 'qa-scenario-'+(Number(index)+1));
  });
  element('assistant-close').addEventListener('click', () => panel.close());
  panel.addEventListener('close', () => { if (opener?.isConnected) opener.focus({preventScroll:true}); });
  panel.addEventListener('click', event => {
    if (event.target !== panel) return;
    const rect = panel.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) panel.close();
  });
  scope.addEventListener('change', () => { if (active) { active.history.focus=scope.value; save(active); } });
  input.addEventListener('input', storeDraft);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault(); form.requestSubmit();
    }
  });
  form.addEventListener('submit', event => { event.preventDefault(); ask(); });
  restart.addEventListener('click', () => ask(true));
  window.addEventListener('review:loaded', ({detail}) => {
    const sameFull = currentFull?.key === detail.key;
    currentFull = detail;
    element('assistant-qa-open').hidden = !detail.report?.lifecycle_checks?.checks?.length;
    if (!active?.isRecheck || !sameFull) bind(detail);
  });
  window.addEventListener('review:recheck-assistant', ({detail}) => { currentRecheck = detail; });
})();
