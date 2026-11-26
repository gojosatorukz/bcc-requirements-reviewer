'use strict';

const { month, push } = reviewCase;
const report = '../../runs/bcc-final/review.html';
const currency = '4\u00A0000\u00A0₸';
const zero = '0\u00A0₸';
const policies = {
  purchase: {
    label: 'По дате покупки',
    month: 'январь',
    balances: [currency, zero],
    rule: 'В расходы включаются проведённые покупки. Месяц учёта определяется по дате совершения покупки в Алматы.',
    acceptance: 'В строке 64 ожидаемый итог после проведения — 4 000 ₸ за январь и 0 ₸ за февраль.',
  },
  posting: {
    label: 'По дате проведения',
    month: 'февраль',
    balances: [zero, currency],
    rule: 'В расходы включаются проведённые покупки. Месяц учёта определяется по дате проведения покупки в Алматы.',
    acceptance: 'В строке 64 сохраняется февральский итог 4 000 ₸. Дополнительно фиксируется январский итог 0 ₸.',
  },
};
const designs = [
  { id: 'registry', name: 'Реестр', description: 'Очередь замечаний и карточка с источниками.', first: 'Статус двух замечаний', work: 'Ежедневное ревью' },
  { id: 'timeline', name: 'Линия времени', description: 'Пять минут вокруг полуночи и два месячных итога.', first: 'Последовательность событий', work: 'Разбор событий и дат' },
  { id: 'dossier', name: 'Досье решения', description: 'Две цитаты на развороте и запись для протокола.', first: 'Формулировки в ТЗ', work: 'Разбор с владельцем правила' },
  { id: 'matrix', name: 'Матрица QA', description: 'Один вход и два ожидаемых результата в таблице.', first: 'Расхождение в приёмке', work: 'Подготовка тестов' },
  { id: 'guided', name: 'Согласование', description: 'Три шага от ситуации до приёмочного примера.', first: 'Ситуация и вопрос', work: 'Обсуждение одного решения' },
  { id: 'board', name: 'Доска решений', description: 'Источники, открытые вопросы и передача в QA.', first: 'Готовность к передаче', work: 'Совместная работа' },
];

const view = document.querySelector('#view');
const galleryLink = document.querySelector('#gallery-link');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const state = { policy: 'purchase', saved: null, step: 0, pushOpen: false };
let current;

function anchor(line, text) {
  return `<a href="${report}#source-line-${line}">${text}</a>`;
}

function status(text, type = 'open') {
  return `<span class="status status-${type}">${text}</span>`;
}

function evidence(item, label) {
  return `<figure class="evidence-card">
    <figcaption><strong>${label}</strong>${anchor(item.source.line, `Строка ${item.source.line}`)}</figcaption>
    <blockquote>${item.source.quote}</blockquote>
    <p class="evidence-outcome">${item.outcome}</p>
  </figure>`;
}

function monthEvidence() {
  return `<div class="evidence-grid">
    ${evidence(month.first, 'Ф‑2 · Правило учёта')}
    ${evidence(month.second, 'Критерий приёмки')}
  </div>`;
}

function balances(key) {
  return `<dl class="balances" aria-label="Счётчики после проведения">
    <div class="${key === 'purchase' ? 'has-spend' : ''}"><dt>Январь</dt><dd>${policies[key].balances[0]}</dd></div>
    <div class="${key === 'posting' ? 'has-spend' : ''}"><dt>Февраль</dt><dd>${policies[key].balances[1]}</dd></div>
  </dl>`;
}

function caseHeading(heading) {
  return `<header class="case-heading">
    <div class="case-meta"><a href="${report}#D-01">${month.id}</a> ${status('Критическое', 'critical')}</div>
    <h1 id="concept-title" tabindex="-1">${heading}</h1>
  </header>`;
}

function pendingPush() {
  return `<details class="push-card"${state.pushOpen ? ' open' : ''}>
    <summary>
      <span class="push-title"><span class="case-code">${push.id}</span><strong>Повторный push после повышения порога</strong></span>
      ${status('Открыт')}<span class="disclosure" aria-hidden="true"></span>
    </summary>
    <div class="push-content">
      <p>${push.scenario}</p>
      <div class="evidence-grid">
        ${evidence(push.first, 'Ф‑3 · Месячный максимум')}
        ${evidence(push.second, 'Ф‑4 · Новый порог')}
      </div>
      <p><strong>${push.question}</strong></p>
      <p class="owner">Адресат · ${push.owner}</p>
      <a href="${report}#D-02">Замечание ${push.id} в отчёте</a>
    </div>
  </details>`;
}

function decisionForm() {
  return `<form class="decision-form" id="decision" tabindex="-1">
    <h2>Вопрос владельцу</h2>
    <p class="decision-question">${month.question}</p>
    <p class="owner">Адресат · ${month.owner}</p>
    <fieldset class="policies">
      <legend>Политика для примера</legend>
      ${Object.entries(policies).map(([key, policy]) => `
        <label class="policy">
          <input type="radio" name="calendar-policy" value="${key}"${state.policy === key ? ' checked' : ''}>
          <span><strong>${policy.label}</strong><small>4 000 ₸ · ${policy.month}</small></span>
        </label>`).join('')}
    </fieldset>
    <div class="actions">
      <button class="action primary" type="submit">${current === 'guided' ? 'Зафиксировать и открыть результат' : 'Зафиксировать пример'}</button>
      <button class="action secondary" data-reset type="button"${state.saved ? '' : ' hidden'}>Сбросить</button>
    </div>
    <p class="decision-feedback" aria-live="polite">${state.saved ? `В примере согласовано · ${policies[state.saved].label.toLowerCase()}.` : ''}</p>
  </form>`;
}

function resultContent() {
  const policy = policies[state.policy];
  const resultState = state.saved === state.policy ? 'Согласовано в примере' : 'Предпросмотр';
  return `<div class="result-top">${status(resultState, state.saved === state.policy ? 'done' : 'draft')}<strong>${policy.label}</strong></div>
    ${balances(state.policy)}
    <dl class="result-notes">
      <div><dt>Ф‑2</dt><dd>${policy.rule} При возврате уменьшаются расходы месяца, в который отнесена исходная покупка.</dd></div>
      <div><dt>Приёмка</dt><dd>${policy.acceptance}</dd></div>
    </dl>`;
}

function resultPanel() {
  return `<section class="result-panel">
    <h2 id="result-title" tabindex="-1">Пример согласования</h2>
    <div data-result>${resultContent()}</div>
    <details class="control-case">
      <summary>Контроль внутри одного месяца</summary>
      <p>${month.agreement_control}</p>
    </details>
  </section>`;
}

function decisionPair() {
  return `<div class="decision-pair">${decisionForm()}${resultPanel()}</div>`;
}

function galleryPreview(id) {
  const previews = {
    registry: `<span class="mini-rail"><b>D‑01</b><b>D‑02</b></span><span class="mini-sheet"><b>Месяц учёта</b><i></i><span class="mini-double"><i></i><i></i></span></span>`,
    timeline: `<span class="mini-track"><b>23:58</b><b>00:03</b></span><span class="mini-wire"></span><span class="mini-months"><b>Январь</b><b>Февраль</b></span>`,
    dossier: `<span class="mini-mast">Досье о покупке</span><span class="mini-columns"><i></i><i></i></span>`,
    matrix: `<span class="mini-table"><b></b><b>Покупка</b><b>Проведение</b><b>Янв</b><b>4 000</b><b>0</b><b>Фев</b><b>0</b><b>4 000</b></span>`,
    guided: `<span class="mini-steps"><i></i><i></i><i></i></span><span class="mini-paper"><b>Какой месяц?</b><i></i></span>`,
    board: `<span class="mini-board"><b>ТЗ<i></i><i></i></b><b>Вопросы<i></i></b><b>QA<i></i></b></span>`,
  };
  return `<div class="mini mini-${id}" aria-hidden="true">${previews[id]}</div>`;
}

function gallery() {
  return `<section class="gallery-hero">
    <div class="hero-inner">
      <div class="hero-intro">
        <p class="overline">BCC-HUB × MNU · Ревью требований</p>
        <h1 tabindex="-1" id="concept-title">Рабочее место<br>аналитика</h1>
        <p>Шесть способов разобрать одну покупку на границе месяца и превратить ответ владельца в проверку для QA.</p>
        <div class="hero-actions">
          <a class="action hero-primary" data-jump href="#concepts">Выбрать концепцию</a>
          <a class="action hero-secondary" data-jump href="#comparison">Сравнить подходы</a>
        </div>
      </div>
      <div class="hero-diagram" role="img" aria-label="Покупка 4 000 тенге в 23:58 31 января, проведение в 00:03 1 февраля. Какой месячный счётчик изменится?">
        <div class="hero-diagram-head"><span>D‑01</span><span>Время Алматы</span></div>
        <span class="hero-amount">${currency}</span>
        <div class="hero-dates"><span><b>23:58</b>31 января · покупка</span><span><b>00:03</b>1 февраля · проведение</span></div>
        <div class="hero-line"><i></i><em></em><i></i></div>
        <div class="hero-months"><b>Январь</b><span>или</span><b>Февраль</b></div>
      </div>
    </div>
  </section>
  <div class="gallery-content">
    <section aria-labelledby="concepts">
      <div class="section-heading">
        <h2 id="concepts" tabindex="-1">Шесть концепций</h2>
        <p>Выбранная политика переносится между вариантами до перезагрузки.</p>
      </div>
      <div class="concept-grid">
        ${designs.map(design => `<a class="concept-card" href="#${design.id}">
          ${galleryPreview(design.id)}
          <div class="concept-caption"><h3>${design.name}</h3><p>${design.description}</p></div>
        </a>`).join('')}
      </div>
    </section>
    <section class="comparison" aria-labelledby="comparison">
      <h2 id="comparison" tabindex="-1">Сравнение подходов</h2>
      <table>
        <thead><tr><th scope="col">Концепция</th><th scope="col">В фокусе</th><th scope="col">Сценарий работы</th></tr></thead>
        <tbody>${designs.map(design => `<tr><th scope="row"><a href="#${design.id}">${design.name}</a></th><td>${design.first}</td><td>${design.work}</td></tr>`).join('')}</tbody>
      </table>
    </section>
  </div>`;
}

function registry() {
  return `<div class="registry-frame">
    <aside class="registry-rail" aria-label="Очередь замечаний">
      <h2>Очередь ревью</h2>
      <p>Уведомление о расходах по карте</p>
      <a class="queue-item active" href="#case-start" data-jump>
        <span class="case-code">${month.id}</span><strong>Месяц учёта покупки</strong>${status(state.saved ? 'Пример согласован' : 'Открыт', state.saved ? 'done' : 'open')}
      </a>
      <button class="queue-item" type="button" data-open-push>
        <span class="case-code">${push.id}</span><strong>Повторный push</strong>${status('Открыт')}
      </button>
    </aside>
    <div class="registry-main" id="case-start" tabindex="-1">
      ${caseHeading('Месяц учёта покупки')}
      <p class="scenario">${month.scenario}</p>
      <h2 class="section-title">Правило и критерий приёмки</h2>
      ${monthEvidence()}
      ${decisionPair()}
      ${pendingPush()}
    </div>
  </div>`;
}

function timeline() {
  return `<div class="timeline-frame">
    <div class="timeline-hero">
      ${caseHeading('Пять минут между месяцами')}
      <p class="scenario">${month.scenario}</p>
      <div class="event-track" aria-label="События по времени Алматы">
        <div><span>31 января</span><strong>23:58</strong><b>Совершена покупка</b></div>
        <div><span>Граница месяца</span><strong>00:00</strong></div>
        <div><span>1 февраля</span><strong>00:03</strong><b>Проведена покупка</b></div>
      </div>
      <div class="timeline-branches">
        <div><strong>Дата покупки · строка 38</strong>${balances('purchase')}</div>
        <div><strong>Дата проведения · строка 64</strong>${balances('posting')}</div>
      </div>
    </div>
    <div class="timeline-main">
      <section>
        <h2 class="section-title">Два основания для учёта</h2>
        ${monthEvidence()}
      </section>
      ${decisionPair()}
      ${pendingPush()}
    </div>
  </div>`;
}

function dossier() {
  return `<article class="dossier-frame">
    <div class="dossier-mast"><strong>Досье решения</strong><span>Уведомление о расходах по карте</span></div>
    ${caseHeading('К какому месяцу относится покупка?')}
    <p class="scenario">${month.scenario}</p>
    <div class="dossier-spread">
      ${evidence(month.first, 'Ф‑2 · Правило учёта')}
      ${evidence(month.second, 'Критерий приёмки')}
    </div>
    <div class="dossier-decision">
      ${decisionForm()}
      ${resultPanel()}
    </div>
    ${pendingPush()}
  </article>`;
}

function matrix() {
  return `<div class="matrix-frame">
    <header class="matrix-header">
      ${caseHeading('Одна покупка, два ожидания QA')}
      <p class="scenario">${month.scenario}</p>
    </header>
    <section class="matrix-body" aria-labelledby="matrix-title">
      <h2 id="matrix-title">Результат после проведения</h2>
      <table class="outcome-table">
        <thead><tr><th scope="col">Проверка</th><th scope="col">По дате покупки</th><th scope="col">По дате проведения</th></tr></thead>
        <tbody>
          <tr><th scope="row">Январь</th><td class="active-amount">${currency}</td><td>${zero}</td></tr>
          <tr><th scope="row">Февраль</th><td>${zero}</td><td class="active-amount">${currency}</td></tr>
          <tr><th scope="row">Опора в ТЗ</th><td>${anchor(38, 'Ф‑2, строка 38')}</td><td>${anchor(64, 'Приёмка, строка 64')}</td></tr>
        </tbody>
      </table>
      ${monthEvidence()}
      ${decisionPair()}
      ${pendingPush()}
    </section>
  </div>`;
}

function guided() {
  const steps = ['Ситуация', 'Выбор правила', 'Результат'];
  const slides = [
    () => `<h2>Где возникло расхождение</h2><p class="scenario">${month.scenario}</p>
      ${monthEvidence()}<p class="decision-question">${month.question}</p>
      <button class="action primary" type="button" data-step="1">Перейти к выбору</button>`,
    () => decisionForm(),
    () => `${resultPanel()}
      <div class="actions"><button class="action secondary" type="button" data-step="1">Вернуться к выбору</button></div>`,
  ];
  return `<div class="guided-frame" data-phase="${state.step}">
    ${caseHeading('К какому месяцу отнести 4 000 ₸?')}
    <nav class="guided-steps" aria-label="Шаги согласования">
      ${steps.map((step, index) => `<button type="button" data-step="${index}"${index === state.step ? ' aria-current="step"' : ''}>${step}</button>`).join('')}
    </nav>
    <section class="guided-paper" id="guided-stage" tabindex="-1">
      ${slides[state.step]()}
    </section>
    ${pendingPush()}
  </div>`;
}

function board() {
  const inQA = state.saved
    ? `<p class="board-note">${status('Согласовано в примере', 'done')} <strong>${month.id} · ${policies[state.saved].label}</strong></p>`
    : `<p class="board-note">${status('Ожидает решения', 'draft')} <strong>${month.id} · Черновик для QA</strong></p>`;
  return `<div class="board-frame">
    <header class="board-header">
      ${caseHeading('От источников к проверке')}
      <p class="scenario">${month.scenario}</p>
    </header>
    <div class="board-columns">
      <section class="board-lane board-sources">
        <h2>Источники</h2>
        ${monthEvidence()}
      </section>
      <section class="board-lane board-questions">
        <h2>На согласовании</h2>
        <p class="board-note"><strong>${month.id} · Месяц учёта</strong></p>
        ${decisionForm()}
        ${pendingPush()}
      </section>
      <section class="board-lane board-result">
        <h2>К передаче в QA</h2>
        ${inQA}
        ${resultPanel()}
      </section>
    </div>
  </div>`;
}

const layouts = { registry, timeline, dossier, matrix, guided, board };

function concept(design) {
  return `<div class="concept-view">
    <nav class="concept-switch" aria-label="Концепции рабочего места">
      ${designs.map(item => `<a href="#${item.id}"${item.id === design.id ? ' aria-current="page"' : ''}>${item.name}</a>`).join('')}
    </nav>
    <p class="demo-note">Демонстрация согласования</p>
    <div class="workspace theme-${design.id}">${layouts[design.id]()}</div>
  </div>`;
}

function render() {
  const design = designs.find(item => item.id === current);
  view.innerHTML = design ? concept(design) : gallery();
  if (design) {
    galleryLink.removeAttribute('aria-current');
  } else {
    galleryLink.setAttribute('aria-current', 'page');
  }
  document.title = `${design ? design.name : 'Галерея рабочего места аналитика'} · Сверка ТЗ`;
}

function focusAt(target) {
  if (!target) return;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: 'start', behavior: reducedMotion.matches ? 'instant' : 'smooth' });
}

function navigate(moveFocus) {
  const fragment = location.hash.slice(1);
  const next = designs.some(item => item.id === fragment) ? fragment : 'gallery';
  if (current !== next) {
    current = next;
    render();
  }
  if (fragment === 'comparison' || fragment === 'concepts') {
    focusAt(document.getElementById(fragment));
  } else if (moveFocus) {
    focusAt(document.getElementById('concept-title'));
  }
}

window.addEventListener('hashchange', () => navigate(true));

document.querySelector('.skip').addEventListener('click', event => {
  event.preventDefault();
  focusAt(document.getElementById('main'));
});

view.addEventListener('click', event => {
  const jump = event.target.closest('[data-jump]');
  if (jump) {
    event.preventDefault();
    const href = jump.getAttribute('href');
    if (current === 'gallery' && location.hash !== href) {
      location.hash = href;
    } else {
      focusAt(document.querySelector(href));
    }
    return;
  }
  const button = event.target.closest('button');
  if (!button) return;
  if (button.hasAttribute('data-open-push')) {
    const details = view.querySelector('.push-card');
    details.open = true;
    focusAt(details.querySelector('summary'));
  }
  if (button.hasAttribute('data-step')) {
    state.step = Number(button.dataset.step);
    render();
    focusAt(view.querySelector('#guided-stage'));
  }
  if (button.hasAttribute('data-reset')) {
    state.saved = null;
    state.policy = 'purchase';
    render();
    focusAt(view.querySelector('.policy input:checked'));
  }
});

view.addEventListener('toggle', event => {
  if (event.target.matches('.push-card')) state.pushOpen = event.target.open;
}, true);

view.addEventListener('change', event => {
  if (event.target.name !== 'calendar-policy') return;
  state.policy = event.target.value;
  view.querySelectorAll('[data-result]').forEach(element => {
    element.innerHTML = resultContent();
  });
  const feedback = view.querySelector('.decision-feedback');
  if (feedback) {
    feedback.textContent = `Предпросмотр · ${policies[state.policy].label.toLowerCase()}.`;
  }
});

view.addEventListener('submit', event => {
  if (event.target.id !== 'decision') return;
  event.preventDefault();
  state.saved = state.policy;
  if (current === 'guided') state.step = 2;
  render();
  focusAt(view.querySelector('#result-title'));
});

navigate(false);
