/* Guide a human through an actual conflict. A choice produces a QA draft in this tab. */
(() => {
  const root = document.getElementById("guided-review");
  if (!root) return;
  const stages = ["Ситуация", "Решение", "Критерий QA"];
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  let cases = [], index = 0, reportUrl, reportIdentity;
  let positions = [];
  let tableHeaders = new Map(), sourceLines = new Map();
  let markupLines = new Set();
  let pages = [];
  let decisionKey, decisions = {};
  let conversationIds = new Set();
  let executions = new Map(), panels = new Map(), panelRequest, panelError = '';

  function readCases(report) {
    const result = [];
    for (const finding of report.whole?.findings ?? []) {
      const critical = finding.priority === "критический";
      const executed = executions.get(finding.id)?.status === 'executed';
      if (finding.category !== "нарушение" || (!critical && !executed)) continue;
      const cited = new Set(finding.evidence.map(item => item.line));
      const witnesses = critical && finding.triage ? (finding.triage.witness ?? []) :
        (finding.candidates ?? []).map(n => report.conflicts?.[n - 1]);
      for (const witness of witnesses) {
        if (witness?.scenario && [witness.first, witness.second].every(side => side?.outcome && cited.has(side.source?.line))) {
          result.push({finding, witness, canRecord:critical && witnesses.length === 1});
        }
      }
    }
    return result;
  }

  function sourcePage(line) {
    return pages.find(page => page.start <= line && line <= page.end);
  }

  function sourceLink(ref, page = sourcePage(ref.line)) {
    const location = page ? ` · с. ${Number(page.page)}` : "";
    return `<a class="guide-ref" href="${escape(reportUrl)}#source-line-${Number(ref.line)}" target="_blank" rel="noopener">Строка ${Number(ref.line)}${location} ↗</a>`;
  }

  function quotation(ref) {
    const quote = ref.quote.trim();
    const header = sourceLines.get(tableHeaders.get(ref.line))?.trim();
    if (header && quote === sourceLines.get(ref.line)?.trim() &&
        [quote, header].every(text => text.startsWith("|") && text.endsWith("|") && !text.includes("\\|"))) {
      const cells = text => text.slice(1, -1).split("|").map(cell => cell.trim());
      const names = cells(header), values = cells(quote);
      if (names.length === values.length && names.every(Boolean)) {
        return `<dl class="guide-table">${values.map((value, i) => `<dt>${escape(names[i])}</dt><dd>${escape(value)}</dd>`).join("")}</dl>`;
      }
    }
    return `<blockquote>${escape(ref.quote)}</blockquote>`;
  }

  function sourceContext(ref, page) {
    if (!page) return "";
    const nearby = [];
    for (let line = Math.max(page.start, ref.line - 2); line <= Math.min(page.end, ref.line + 2); line++) {
      if (line === ref.line || markupLines.has(line) || !sourceLines.get(line)?.trim()) continue;
      nearby.push({line, quote: sourceLines.get(line)});
    }
    if (!nearby.length) return "";
    return `<details class="guide-source-context"><summary>Соседние строки на с. ${Number(page.page)}</summary>${
      nearby.map(row => `<div class="guide-context-row">${sourceLink(row, page)}${quotation(row)}</div>`).join("")
    }</details>`;
  }

  function evidence(witness) {
    return `<aside class="guide-evidence" aria-label="Два основания в ТЗ">${[witness.first, witness.second].map(({source}) => {
      const page = sourcePage(source.line);
      return `<figure><figcaption><span class="guide-origin">${tableHeaders.has(source.line) ? "Из таблицы" : "Из документа"}</span><span class="guide-chosen">Основание выбора</span> ${sourceLink(source, page)}</figcaption>${quotation(source)}${sourceContext(source, page)}</figure>`;
    }).join("")}</aside>`;
  }

  function displayPanel() {
    const slot = root.querySelector('.guide-calculation');
    if (!slot) return;
    const panel = panels.get(cases[index].finding.id);
    if (panel) {
      slot.removeAttribute('aria-busy');
      slot.replaceChildren(panel);
    } else if (panelError) {
      slot.removeAttribute('aria-busy');
      slot.textContent = panelError;
    }
  }

  async function loadPanels(key) {
    const match = /^(demo|[0-9a-f]{32}):([0-9a-f]{64})$/.exec(key ?? '');
    if (!match) {
      panelError = 'Для проверки расчёта нужен полный отчёт с идентификатором версии.';
      displayPanel();
      return;
    }
    const ownRequest = new AbortController();
    panelRequest = ownRequest;
    try {
      const response = await fetch('/reports/' + match[1] + '/executions/' + match[2],
        {cache:'no-store', signal:ownRequest.signal});
      if (!response.ok) throw new Error();
      const saved = await response.json();
      if (panelRequest !== ownRequest) return;
      if (saved.sha256 !== match[2]) throw new Error();
      const fresh = new Map();
      for (const [fid, item] of executions) {
        // This same-origin endpoint renders escaped, replay-validated report data with
        // the offline HTML renderer. Links should open the matching standalone source.
        const template = document.createElement('template');
        if (typeof saved.items?.[fid] !== 'string') throw new Error();
        template.innerHTML = saved.items[fid];
        const panel = template.content.firstElementChild;
        if (panel?.id !== 'execution-' + fid) throw new Error();
        for (const link of panel.querySelectorAll('a[href^="#source-line-"]')) {
          link.setAttribute('href', reportUrl + link.getAttribute('href'));
          link.target = '_blank';
          link.rel = 'noopener';
          link.setAttribute('aria-label', link.textContent + ' · открыть полный отчёт в новой вкладке');
        }
        window.dispatchEvent(new CustomEvent('review:execution-panel', {detail:{panel, item, reportUrl,
          endpoint:'/api/simulate/' + match[1] + '/' + match[2] + '/' + fid}}));
        fresh.set(fid, panel);
      }
      panels = fresh;
    } catch {
      if (panelRequest !== ownRequest) return;
      panelError = 'Не удалось проверить расчёт для текущей версии отчёта. Обновите страницу.';
    }
    displayPanel();
  }

  function render(focus = false) {
    const {finding, witness, canRecord} = cases[index];
    const state = positions[index];
    const {stage, side} = state;
    const outcomes = [witness.first, witness.second];
    const source = outcomes[side]?.source;
    const execution = executions.get(finding.id);
    const calculated = execution?.status === 'executed';
    const hasDialogue = conversationIds.has(finding.id);
    const saved = hasDialogue ? null : decisions[finding.id];
    const current = side === 0 ? "first" : "second";
    const draft = source ? `Сценарий: ${witness.scenario}\nОжидаемый результат: ${outcomes[side].outcome}\nОснование: строка ${source.line} ТЗ.\nВопрос: ${finding.question}` : "";
    const selector = cases.length > 1
      ? `<label class="guide-select">Конфликт <select aria-label="Выбрать конфликт">${cases.map(({finding: f}, i) => `<option value="${i}" ${i === index ? "selected" : ""}>${escape(f.id)} · ${escape(f.title ?? ((i+1) + ' из ' + cases.length))}</option>`).join("")}</select></label>`
      : `<span class="guide-select">Конфликт ${escape(finding.id)}</span>`;
    let body;
    if (stage === 0) {
      body = `<h2 tabindex="-1">${escape(finding.question)}</h2><p class="guide-scenario">${escape(witness.scenario)}</p>
      <button type="button" data-act="next" class="guide-primary">${calculated ? 'Выбрать исход' : 'Сравнить исходы'} <span aria-hidden="true">→</span></button>`;
    } else if (stage === 1) {
      body = `<h2 tabindex="-1">Какой исход принять?</h2><p class="guide-context">${escape(finding.question)}</p>
      <fieldset class="guide-options"><legend>Ожидаемый результат</legend>${outcomes.map((outcome,i) =>
        `<label><input name="guide-choice" type="radio" value="${i}" ${side === i ? "checked" : ""}><span>${escape(outcome.outcome)}</span></label>`).join("")}</fieldset>
      <div class="guide-actions"><button type="button" data-act="next" class="guide-primary" ${side === null ? "disabled" : ""}>Подготовить критерий <span aria-hidden="true">→</span></button><button type="button" class="guide-back" data-act="back">Назад</button></div>`;
    } else {
      body = `<h2 tabindex="-1">Черновик для QA</h2><dl class="guide-draft"><dt>Сценарий</dt><dd>${escape(witness.scenario)}</dd><dt>Ожидаемый результат</dt><dd>${escape(outcomes[side].outcome)}</dd><dt>Основание</dt><dd>${sourceLink(source)}</dd></dl>
      <div class="guide-actions"><button type="button" class="guide-primary" data-act="copy">Скопировать критерий</button><button type="button" class="guide-back" data-act="back">Изменить черновик</button></div>
      ${decisionKey && canRecord && !hasDialogue ? `<div class="guide-record"><p role="status">${saved ? (saved === current ? "Выбранный исход зафиксирован для этой версии отчёта." :
         "Для этой версии отчёта зафиксирован другой исход. Можно заменить его.") : "Решение владельца пока не зафиксировано."}</p>
        ${saved !== current ? `<button type="button" class="guide-primary" data-act="record">${saved ? "Заменить решение владельца" : "Зафиксировать решение владельца"}</button>` : ""}
        ${saved ? '<button type="button" class="guide-back" data-act="cancel">Отменить фиксацию</button>' : ""}</div>` : ""}
      <div class="guide-copy-status" role="status"></div>`;
    }
    if (decisionKey) {
      body += `<button type="button" class="guide-back" data-act="answer">${hasDialogue ? "Открыть переписку с владельцем" : "Ответить своими словами"}</button>`;
    }
    if (calculated && stage > 0) {
      body += `<a class="guide-back" href="${escape(reportUrl)}#execution-${escape(finding.id)}" target="_blank" rel="noopener">Открыть расчёт</a>`;
    }
    root.innerHTML = `<div class="guide-wrap"><div class="guide-bar"><ol aria-label="Шаги согласования">${stages.map((label,i) =>
      `<li ${i === stage ? 'aria-current="step"' : ""} class="${i <= stage ? "reached" : ""}"><span>${label}</span></li>`).join("")}</ol>${selector}</div>
      <div class="guide-grid${execution && stage === 0 ? ' with-calculation' : ''}"><article class="guide-sheet" data-stage="${stage}">${body}</article>${
        execution && stage === 0 ? `<div><div class="guide-calculation" aria-busy="true">Проверяем сохранённый расчёт…</div>${calculated ? '' : evidence(witness)}</div>` : evidence(witness)
      }</div></div>`;
    displayPanel();
    function highlightSource() {
      root.querySelectorAll(".guide-evidence figure").forEach((card, i) => {
        card.classList.toggle("selected", stage > 0 && state.side === i);
      });
    }
    highlightSource();
    root.querySelector("select")?.addEventListener("change", event => { index = Number(event.target.value); render(true); });
    root.querySelectorAll('input[type="radio"]').forEach(input => input.addEventListener("change", () => {
      state.side = Number(input.value);
      highlightSource();
      root.querySelector('[data-act="next"]').disabled = false;
    }));
    root.querySelector('[data-act="next"]')?.addEventListener("click", () => { state.stage++; render(true); });
    root.querySelector('[data-act="back"]')?.addEventListener("click", () => { state.stage--; render(true); });
    root.querySelector('[data-act="answer"]')?.addEventListener("click", () =>
      window.dispatchEvent(new CustomEvent("review:write-answer", {detail:{key:decisionKey, finding:finding.id}})));
    function record(chosen) {
      if (chosen === null) delete decisions[finding.id];
      else decisions[finding.id] = chosen;
      window.dispatchEvent(new CustomEvent("review:decision", {detail:{key:decisionKey, finding:finding.id, side:chosen}}));
      render(true);
    }
    root.querySelector('[data-act="record"]')?.addEventListener("click", () => record(current));
    root.querySelector('[data-act="cancel"]')?.addEventListener("click", () => record(null));
    root.querySelector('[data-act="copy"]')?.addEventListener("click", async () => {
      const status = root.querySelector(".guide-copy-status");
      try {
        await navigator.clipboard.writeText(draft);
        status.textContent = "Критерий скопирован";
      } catch {
        status.innerHTML = '<label>Скопируйте выделенный критерий<textarea readonly></textarea></label>';
        const field = status.querySelector("textarea");
        field.value = draft;
        field.select();
      }
    });
    if (focus) root.querySelector("h2").focus({preventScroll:true});
  }

  window.addEventListener("review:loaded", ({detail}) => {
    const {report, reportUrl: url, key} = detail;
    const identity = key || [url, report.run?.input_sha256, report.run?.started_utc].join("|");
    if (identity === reportIdentity) return;
    reportIdentity = identity;
    reportUrl = url;
    decisionKey = key;
    panelRequest?.abort();
    panelRequest = null;
    panelError = '';
    executions = new Map((report.executables?.items ?? []).map(item => [item.finding_id, item]));
    panels = new Map();
    conversationIds = new Set();
    decisions = {...detail.decisions};
    pages = report.run?.pages ?? [];
    sourceLines = new Map((report.source ?? []).map(line => [line.line, line.text]));
    tableHeaders = new Map((report.segments ?? []).filter(segment => segment.kind === "строка таблицы")
      .map(segment => [segment.start, segment.header]));
    markupLines = new Set((report.segments ?? []).filter(segment => segment.kind === "разметка")
      .map(segment => segment.start));
    cases = readCases(report);
    positions = cases.map(({finding, canRecord}) => {
      const saved = canRecord && decisions[finding.id];
      return saved === "first" || saved === "second" ? {stage:2, side:saved === "first" ? 0 : 1} : {stage:0, side:null};
    });
    index = 0;
    root.hidden = !cases.length;
    if (cases.length) render(); else root.replaceChildren();
    window.dispatchEvent(new CustomEvent('review:guide-findings', {detail:{key, ids:cases.map(c => c.finding.id)}}));
    if (cases.length && executions.size) loadPanels(key);
  });

  window.addEventListener('review:show-finding', ({detail}) => {
    if (detail.key !== decisionKey) return;
    const next = cases[index]?.finding.id === detail.finding ? index : cases.findIndex(c => c.finding.id === detail.finding);
    if (next < 0) return;
    index = next;
    positions[index].stage = 0;
    render(true);
    root.scrollIntoView({block:'start'});
  });

  window.addEventListener("review:dialogue", ({detail}) => {
    if (detail.key !== decisionKey || !cases.length) return;
    conversationIds = new Set(detail.ids);
    render();
  });
})();
