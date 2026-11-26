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

  function readCases(report) {
    const result = [];
    for (const finding of report.whole?.findings ?? []) {
      if (finding.priority !== "критический" || finding.category !== "нарушение") continue;
      const cited = new Set(finding.evidence.map(item => item.line));
      const witnesses = finding.triage?.witness ?? (finding.candidates ?? []).map(n => report.conflicts?.[n - 1]);
      for (const witness of witnesses) {
        if (witness?.scenario && [witness.first, witness.second].every(side => side?.outcome && cited.has(side.source?.line))) {
          result.push({finding, witness});
        }
      }
    }
    return result;
  }

  function sourceLink(ref) {
    return `<a class="guide-ref" href="${escape(reportUrl)}#source-line-${Number(ref.line)}" target="_blank" rel="noopener">Строка ${Number(ref.line)} ↗</a>`;
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

  function evidence(witness) {
    return `<aside class="guide-evidence" aria-label="Два основания в ТЗ">${[witness.first, witness.second].map(side =>
      `<figure><figcaption><span>${tableHeaders.has(side.source.line) ? "Из таблицы" : "Из документа"}</span> ${sourceLink(side.source)}</figcaption>${quotation(side.source)}</figure>`).join("")}</aside>`;
  }

  function render(focus = false) {
    const {finding, witness} = cases[index];
    const state = positions[index];
    const {stage, side} = state;
    const outcomes = [witness.first, witness.second];
    const source = outcomes[side]?.source;
    const draft = source ? `Сценарий: ${witness.scenario}\nОжидаемый результат: ${outcomes[side].outcome}\nОснование: строка ${source.line} ТЗ.\nВопрос: ${finding.question}` : "";
    const selector = cases.length > 1
      ? `<label class="guide-select">Конфликт <select aria-label="Выбрать конфликт">${cases.map(({finding: f}, i) => `<option value="${i}" ${i === index ? "selected" : ""}>${escape(f.id)} · ${i+1} из ${cases.length}</option>`).join("")}</select></label>`
      : `<span class="guide-select">Конфликт ${escape(finding.id)}</span>`;
    let body;
    if (stage === 0) {
      body = `<h2 tabindex="-1">${escape(finding.question)}</h2><p class="guide-scenario">${escape(witness.scenario)}</p>
      <button type="button" data-act="next" class="guide-primary">Сравнить исходы <span aria-hidden="true">→</span></button>`;
    } else if (stage === 1) {
      body = `<h2 tabindex="-1">Какой исход принять?</h2><p class="guide-context">${escape(finding.question)}</p>
      <fieldset class="guide-options"><legend>Ожидаемый результат</legend>${outcomes.map((outcome,i) =>
        `<label><input name="guide-choice" type="radio" value="${i}" ${side === i ? "checked" : ""}><span>${escape(outcome.outcome)}</span></label>`).join("")}</fieldset>
      <div class="guide-actions"><button type="button" data-act="next" class="guide-primary" ${side === null ? "disabled" : ""}>Подготовить критерий <span aria-hidden="true">→</span></button><button type="button" class="guide-back" data-act="back">Назад</button></div>`;
    } else {
      body = `<h2 tabindex="-1">Черновик для QA</h2><dl class="guide-draft"><dt>Сценарий</dt><dd>${escape(witness.scenario)}</dd><dt>Ожидаемый результат</dt><dd>${escape(outcomes[side].outcome)}</dd><dt>Основание</dt><dd>${sourceLink(source)}</dd></dl>
      <div class="guide-actions"><button type="button" class="guide-primary" data-act="copy">Скопировать критерий</button><button type="button" class="guide-back" data-act="back">Изменить решение</button></div>
      <div class="guide-copy-status" role="status"></div>`;
    }
    root.innerHTML = `<div class="guide-wrap"><div class="guide-bar"><ol aria-label="Шаги согласования">${stages.map((label,i) =>
      `<li ${i === stage ? 'aria-current="step"' : ""} class="${i <= stage ? "reached" : ""}"><span>${label}</span></li>`).join("")}</ol>${selector}</div>
      <div class="guide-grid"><article class="guide-sheet" data-stage="${stage}">${body}</article>${evidence(witness)}</div></div>`;
    root.querySelector("select")?.addEventListener("change", event => { index = Number(event.target.value); render(true); });
    root.querySelectorAll('input[type="radio"]').forEach(input => input.addEventListener("change", () => {
      state.side = Number(input.value);
      root.querySelector('[data-act="next"]').disabled = false;
    }));
    root.querySelector('[data-act="next"]')?.addEventListener("click", () => { state.stage++; render(true); });
    root.querySelector('[data-act="back"]')?.addEventListener("click", () => { state.stage--; render(true); });
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
    const {report, reportUrl: url} = detail;
    const identity = [url, report.run?.input_sha256, report.run?.started_utc].join("|");
    if (identity === reportIdentity) return;
    reportIdentity = identity;
    reportUrl = url;
    sourceLines = new Map((report.source ?? []).map(line => [line.line, line.text]));
    tableHeaders = new Map((report.segments ?? []).filter(segment => segment.kind === "строка таблицы")
      .map(segment => [segment.start, segment.header]));
    cases = readCases(report);
    positions = cases.map(() => ({stage:0, side:null}));
    index = 0;
    root.hidden = !cases.length;
    if (cases.length) render(); else root.replaceChildren();
  });
})();
