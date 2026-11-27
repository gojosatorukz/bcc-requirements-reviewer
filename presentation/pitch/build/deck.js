const React = require("react"), RDS = require("react-dom/server"), lu = require("react-icons/lu");
const QR = require("qrcode"), fs = require("fs"), path = require("path");
const OUT = process.argv[2];
const ic = (n, c = "#19161d", s = 34) => RDS.renderToStaticMarkup(React.createElement(lu[n], { color: c, size: s, strokeWidth: 2.2 }));
const face = (cls = "") => `<span class="face ${cls}"><span class="eye l"></span><span class="eye r"></span><span class="mouth"></span></span>`;
const ref = t => `<span class="ref">${t}</span>`;

// flat illustrated analyst (no photos)
const aidana = `<svg viewBox="0 0 360 400" class="aidana" xmlns="http://www.w3.org/2000/svg">
 <circle cx="180" cy="200" r="170" fill="#ffe148"/>
 <path d="M70 400 C75 300 120 265 180 265 C240 265 285 300 290 400 Z" fill="#4036bd" stroke="#19161d" stroke-width="5"/>
 <path d="M150 268 L180 310 L210 268" fill="#fff8e9" stroke="#19161d" stroke-width="5" stroke-linejoin="round"/>
 <rect x="160" y="225" width="40" height="48" rx="12" fill="#e9b48e" stroke="#19161d" stroke-width="5"/>
 <ellipse cx="180" cy="175" rx="62" ry="70" fill="#f2c39d" stroke="#19161d" stroke-width="5"/>
 <path d="M116 170 C110 100 160 82 192 92 C232 100 252 135 244 175 C230 140 200 125 168 122 C150 135 132 150 116 170 Z" fill="#19161d"/>
 <circle cx="200" cy="82" r="28" fill="#19161d"/>
 <circle cx="158" cy="180" r="6" fill="#19161d"/><circle cx="204" cy="180" r="6" fill="#19161d"/>
 <path d="M166 212 Q181 222 196 212" fill="none" stroke="#19161d" stroke-width="5" stroke-linecap="round"/>
 <circle cx="140" cy="200" r="9" fill="#ff612f" opacity=".45"/><circle cx="222" cy="200" r="9" fill="#ff612f" opacity=".45"/>
 <g transform="rotate(-8 270 300)"><rect x="215" y="250" width="110" height="140" rx="10" fill="#fffcf4" stroke="#19161d" stroke-width="5"/>
 <line x1="235" y1="280" x2="305" y2="280" stroke="#19161d" stroke-width="5" stroke-linecap="round"/>
 <line x1="235" y1="305" x2="295" y2="305" stroke="#ff612f" stroke-width="5" stroke-linecap="round"/>
 <line x1="235" y1="330" x2="305" y2="330" stroke="#19161d" stroke-width="5" stroke-linecap="round"/>
 <line x1="235" y1="355" x2="280" y2="355" stroke="#4036bd" stroke-width="5" stroke-linecap="round"/></g>
</svg>`;

const css = `
@font-face{font-family:Rubik;src:url("Rubik.ttf") format("truetype");font-weight:300 900}
@page{size:1600px 900px;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
:root{--orange:#ff612f;--yellow:#ffe148;--paper:#fff8e9;--panel:#fffcf4;--ink:#19161d;--indigo:#4036bd;--muted:#6b5a66;--peach:#ffd9b8;--lav:#e4ddff;--green:#1f7a52}
html,body{background:#333}
body{font-family:Rubik,system-ui,sans-serif;color:var(--ink);-webkit-print-color-adjust:exact;print-color-adjust:exact}
.slide{width:1600px;height:900px;position:relative;overflow:hidden;page-break-after:always;break-after:page;background:var(--paper);padding:70px 90px}
.slide.o{background:var(--orange)} .slide.k{background:var(--ink);color:var(--paper)}
.sun{position:absolute;border-radius:50%;background:var(--yellow);z-index:0}
.slide>*{position:relative;z-index:1}
.slide>.sun{position:absolute;z-index:0}
.kick{font-weight:760;font-size:20px;letter-spacing:.14em;text-transform:uppercase;color:var(--orange);margin-bottom:14px}
.o .kick{color:var(--ink)} .k .kick{color:var(--yellow)}
h1{font-weight:780;font-size:64px;line-height:1;letter-spacing:-.04em}
h1 .dot{display:inline-block;width:.22em;height:.22em;background:var(--panel);margin-left:.04em}
h2{font-weight:770;font-size:52px;line-height:1.02;letter-spacing:-.035em;max-width:1420px}
.lead{font-size:26px;line-height:1.3;max-width:1000px;margin-top:18px}
.card{background:var(--panel);border:3px solid var(--ink);border-radius:26px;box-shadow:5px 8px 0 var(--ink);padding:30px}
.tilt{transform:rotate(-2.5deg)} .tilt2{transform:rotate(2deg)}
.ref{display:inline-block;font-weight:700;font-size:16px;border:2px solid var(--ink);border-radius:99px;padding:4px 12px;background:var(--panel);white-space:nowrap}
.btn{display:inline-flex;align-items:center;gap:12px;background:var(--indigo);color:#fff;font-weight:700;font-size:22px;border-radius:14px;padding:16px 24px;border:3px solid var(--ink);box-shadow:4px 6px 0 var(--ink)}
.foot{position:absolute;left:90px;right:90px;bottom:32px;display:flex;justify-content:space-between;font-size:15px;font-weight:600;opacity:.65;z-index:2}
.brand{display:flex;align-items:center;gap:10px;font-weight:820}
.emb{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;transform:rotate(-6deg);background:var(--ink);color:var(--orange);font-size:18px;font-weight:900}
.k .emb{background:var(--orange);color:var(--ink)}
.face{display:inline-block;position:relative;width:120px;height:114px;border-radius:53% 48% 45% 47%;background:var(--ink);transform:rotate(-6deg)}
.face .eye{position:absolute;top:26%;width:25%;height:28%;border-radius:50%;background:radial-gradient(circle at 61% 52%,var(--ink) 0 25%,#fffaf0 30%)}
.face .eye.l{left:22%}.face .eye.r{right:22%}
.face .mouth{position:absolute;width:30%;height:16%;left:35%;top:60%;border:5px solid #ffe59d;border-top:0;border-radius:0 0 60px 60px}
.face.big{width:260px;height:248px}.face.big .mouth{border-width:9px}
.face.sm{width:64px;height:60px}.face.sm .mouth{border-width:3px}
.bubble{position:relative;background:var(--panel);border:3px solid var(--ink);border-radius:22px;padding:18px 22px;font-size:22px;font-weight:600;box-shadow:4px 6px 0 var(--ink)}
.grid{display:grid;gap:28px}
.num{font-weight:800;letter-spacing:-.04em;line-height:1}
.muted{color:var(--muted)}
.ic{display:grid;place-items:center;width:64px;height:64px;border-radius:18px;background:var(--ink);transform:rotate(-6deg);flex:none}
.pill{display:inline-block;background:var(--ink);color:var(--yellow);font-weight:700;font-size:18px;padding:6px 14px;border-radius:99px}
ul.b{list-style:none} ul.b li{position:relative;padding-left:26px;margin:9px 0;font-size:21px;line-height:1.3}
ul.b li::before{content:"";position:absolute;left:0;top:10px;width:12px;height:12px;border-radius:4px;background:var(--orange);transform:rotate(-8deg)}
.route{display:flex;align-items:center;gap:14px}
.arrow{font-size:36px;font-weight:800}
.dash{border-top:3px dashed #9d8874;margin:14px 0}
.tag{font-size:15px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
table.cmp{border-collapse:separate;border-spacing:0;width:100%;font-size:20px;background:var(--panel);border:3px solid var(--ink);border-radius:22px;overflow:hidden;box-shadow:5px 8px 0 var(--ink)}
table.cmp td,table.cmp th{padding:15px 18px;border-bottom:2px solid #e8dccb;text-align:left;vertical-align:middle}
table.cmp th{font-size:19px;background:#f1ece2}
table.cmp th.us,table.cmp td.us{background:#fff1c2;font-weight:700}
table.cmp tr:last-child td{border-bottom:0}
.no{color:#c43d1a}.yes{color:var(--green)}
`;

const foot = (n, dark) => `<div class="foot"><span class="brand"><span class="emb">#</span>Разбор ТЗ</span><span>BCC-HUB × MNU FinTech Hackathon 2026 · ${n}</span></div>`;
const S = [];

// 1 COVER
S.push(`<section class="slide o"><div class="sun" style="width:900px;height:900px;right:-220px;bottom:-420px"></div>
<div class="kick">BCC-HUB × MNU FinTech Hackathon 2026</div>
<h1 style="font-size:150px;margin-top:60px">Разбор ТЗ<span class="dot"></span></h1>
<p class="lead" style="font-size:34px;max-width:820px;margin-top:30px">AI-агент находит противоречия в банковском ТЗ <b>до того</b>, как их найдут разработчик, QA или клиент</p>
<div style="display:flex;gap:14px;margin-top:44px">${ref("строка 38 ≠ строка 64")}${ref("PDF · DOCX · MD · TXT")}${ref("демо-отчёт: GPT-6.2 Astra")}${ref("отчёт на русском")}</div>
<div style="position:absolute;right:170px;top:230px">${face("big")}</div>
<div class="bubble" style="position:absolute;right:140px;top:70px;transform:rotate(-3deg)">Где два пункта дают разный результат?</div>
${foot(1)}</section>`);

// 2 STORY
S.push(`<section class="slide o"><div class="sun" style="width:700px;height:700px;right:-180px;top:-260px"></div>
<div class="kick">История · одна покупка из официального ТЗ</div>
<h2 style="max-width:700px">31 января, 23:58. Покупка на 4&nbsp;000 ₸</h2>
<p class="lead" style="max-width:640px">Банк провёл её через пять минут, уже <b>1 февраля</b>. Сколько клиент увидит в расходах за февраль?</p>
<div class="card" style="margin-top:44px;max-width:640px;font-size:22px;line-height:1.35">Строка 38 задаёт правило: месяц — по дате покупки. Строка 64 — приёмочный пример: по нему в феврале должно быть 4&nbsp;000 ₸. <b>Разработчик</b> делает по правилу, <b>QA</b> проверяет по примеру, и расхождение всплывёт на тестировании.</div>
<div class="card tilt" style="position:absolute;right:110px;top:150px;width:620px;padding:40px">
 <div style="display:flex;justify-content:space-between" class="tag"><span>BCC-HUB × MNU</span><span style="text-transform:none;letter-spacing:0;font-weight:500">Банковский пример</span></div>
 <div class="num" style="font-size:58px;margin:26px 0 18px">Одна покупка.<br>Два итога.</div>
 <div style="display:flex;justify-content:space-between;font-size:22px"><span>Покупка</span><b>31 января, 23:58</b></div>
 <div style="display:flex;justify-content:space-between;font-size:22px;margin-top:6px"><span>Проведение</span><b>1 февраля, 00:03</b></div>
 <div class="dash"></div><div style="font-size:24px;font-weight:700">Расходы за февраль</div>
 <div style="display:grid;grid-template-columns:1fr 1fr;margin-top:12px">
  <div style="border-right:2px solid #d8cbb8;padding-right:20px"><div style="font-size:18px">Ф-2 · по дате покупки</div><div class="num" style="font-size:76px;white-space:nowrap">0 ₸</div>${ref("строка 38")}</div>
  <div style="padding-left:24px"><div style="font-size:18px">Приёмка · по проведению</div><div class="num" style="font-size:76px;white-space:nowrap">4&nbsp;000 ₸</div>${ref("строка 64")}</div>
 </div></div>
${foot(2)}</section>`);

// 3 HOW WE CAME
const tl = [["25 ноября", "Получили ТЗ банка", "Обычный учебный документ: уведомление о расходах по карте"],
 ["Первое чтение", "Нашли спор строк 38 и 64", "Две части одного ТЗ дают 0 ₸ и 4&nbsp;000 ₸ за одну покупку"],
 ["Инсайт", "Два итога на одной операции", "В выданном банковском ТЗ правило и приёмка дали разные результаты для одной покупки"],
 ["26–27 ноября", "Построили агента", "Модель рассуждает, код проверяет цитаты и считает исходы. Проверили также на синтетических контрольных ТЗ"]];
S.push(`<section class="slide"><div class="kick">Как мы пришли к идее</div>
<h2>Мы сами споткнулись о ТЗ хакатона</h2>
<div class="grid" style="grid-template-columns:repeat(4,1fr);margin-top:70px;gap:30px">
${tl.map(([d, h, t], i) => `<div class="card ${i % 2 ? "tilt2" : "tilt"}" style="padding:28px;${i === 1 ? "background:var(--yellow)" : ""}"><div class="pill">${d}</div><div style="font-size:30px;font-weight:760;margin:20px 0 12px;line-height:1.08">${h}</div><div style="font-size:20px;line-height:1.35">${t}</div></div>`).join("")}
</div>
<div style="display:flex;align-items:center;gap:24px;margin-top:70px">${face("sm")}<div class="bubble" style="font-size:24px">Если две строки спорят, нужен не пересказ, а <b>расчёт</b> и <b>вопрос</b> тому, кто решает.</div></div>
${foot(3)}</section>`);

// 4 USER
S.push(`<section class="slide"><div class="kick">Пользователь</div>
<h2>Айдана, системный аналитик карточного продукта</h2>
<div style="display:grid;grid-template-columns:400px 1fr;gap:50px;margin-top:40px;align-items:start">
<div>${aidana}<div class="muted" style="font-size:16px;margin-top:6px;font-style:italic">Типовой портрет пользователя</div></div>
<div class="grid" style="grid-template-columns:repeat(3,1fr);gap:24px">
${[["LuBriefcase", "Её работа", ["Пишет ТЗ на доработки мобильного банка", "Сводит правило, API и приёмку", "Согласует с владельцем, разработкой и QA"], "var(--panel)"],
   ["LuHeartCrack", "Что болит", ["Противоречия всплывают на тестировании", "Спор «кто прав» без доказательств", "Чек-лист ревью заполнить некогда"], "var(--peach)"],
   ["LuTarget", "Что ей нужно", ["Увидеть конфликт до разработки", "Точный вопрос владельцу, а не «уточните»", "Ожидаемый результат для QA"], "var(--lav)"]]
  .map(([i, h, l, bg]) => `<div class="card" style="background:${bg};padding:26px"><div class="ic">${ic(i, "#ffe148", 30)}</div><div style="font-size:28px;font-weight:760;margin:20px 0 6px">${h}</div><ul class="b">${l.map(x => `<li>${x}</li>`).join("")}</ul></div>`).join("")}
<div class="card" style="grid-column:1/-1;padding:20px 26px;font-size:21px;background:var(--yellow)"><b>Когда:</b> перед передачей задачи разработчику и QA · <b>покупатель пилота:</b> руководитель аналитики или QA с бюджетом · <b>допуск данных:</b> ИБ</div>
</div></div>${foot(4)}</section>`);

// 5 PAIN
S.push(`<section class="slide o"><div class="sun" style="width:620px;height:620px;left:-200px;bottom:-300px"></div>
<div class="kick">Боль</div><h2>Противоречия в требованиях — отраслевая боль</h2>
<div style="display:grid;grid-template-columns:520px 1fr;gap:60px;margin-top:30px;align-items:end">
<div><div class="num" style="font-size:118px;white-space:nowrap;color:var(--panel);-webkit-text-stroke:4px var(--ink);text-shadow:6px 8px 0 var(--ink)">44 из 228</div>
<div style="font-size:26px;font-weight:600;margin-top:16px;line-height:1.25">компаний включили противоречивые требования в пять главных проблем разработки</div>
<div style="font-size:15px;margin-top:10px;opacity:.75">Международное исследование NaPiRE, 10 стран · чем позже найдена ошибка, тем дороже исправление (Boehm, Basili, 2001)</div></div>
<div style="display:flex;align-items:flex-end;gap:22px;height:280px">
${[["ТЗ", 70, "var(--yellow)", "здесь агент"], ["Разработка", 120, "var(--panel)"], ["Тест", 170, "var(--panel)"], ["Прод и клиент", 230, "var(--ink)"]].map(([l, h, bg, note]) =>
 `<div style="flex:1;text-align:center">${note ? `<div class="pill" style="margin-bottom:10px">${note}</div>` : ""}<div style="height:${h}px;background:${bg};border:3px solid var(--ink);border-radius:18px 18px 0 0;box-shadow:4px 0 0 var(--ink)"></div><div style="font-size:21px;font-weight:700;margin-top:10px">${l}</div></div>`).join("")}
</div></div>
<div class="grid" style="grid-template-columns:1fr 1fr 1fr;margin-top:28px;gap:26px">
<div class="card" style="padding:22px"><div class="num" style="font-size:44px">14 пунктов</div><div style="font-size:19px;margin-top:6px">чек-листа ревью оставлены пустыми в выданном учебном ТЗ</div></div>
<div class="card" style="padding:22px"><div class="num" style="font-size:44px">26 строк</div><div style="font-size:19px;margin-top:6px">между правилом и приёмкой, которые спорят: глазами легко пропустить</div></div>
<div class="card" style="padding:22px"><div class="num" style="font-size:44px">нет расчёта</div><div style="font-size:19px;margin-top:6px">чат с LLM может процитировать строку, но не исполнит два правила на одном примере</div></div>
</div>${foot(5)}</section>`);

// 6 HOW IT SOLVES
const steps = [["LuFileText", "Читает", "PDF, DOCX, MD, TXT. Таблицы и JSON-примеры API"], ["LuScissors", "Нарезает", "ID отчёта, ID документа (Ф-1…) и дословная цитата"],
 ["LuListChecks", "Проверяет", "Каждое требование и весь набор по выбранным критериям ISO/IEC/IEEE 29148 и IEEE 830"], ["LuCalculator", "Доказывает", "Две программы правил на одном примере + контроль"],
 ["LuMessageCircleQuestion", "Спрашивает", "Вопрос владельцу, затем перепроверка новой редакции"]];
S.push(`<section class="slide"><div class="kick">Как решаем</div><h2>От файла ТЗ до решения владельца за 5 шагов</h2>
<div class="grid" style="grid-template-columns:repeat(5,1fr);gap:22px;margin-top:60px">
${steps.map(([i, h, t], k) => `<div class="card ${k === 3 ? "" : ""}" style="padding:26px;${k === 3 ? "background:var(--yellow)" : ""}"><div style="display:flex;justify-content:space-between;align-items:center"><div class="ic">${ic(i, "#ffe148", 30)}</div><div class="num" style="font-size:60px;color:var(--orange)">${k + 1}</div></div><div style="font-size:30px;font-weight:760;margin:24px 0 10px">${h}</div><div style="font-size:20px;line-height:1.35">${t}</div></div>`).join("")}
</div>
<div class="card" style="margin-top:46px;background:var(--ink);color:var(--paper);display:flex;align-items:center;gap:26px;padding:26px 32px">${ic("LuShieldCheck", "#ffe148", 46)}<div style="font-size:23px;line-height:1.35"><b style="color:var(--yellow)">Код</b> проверяет привязку каждой цитаты к строке и пересчитывает поддерживаемые примеры. Верность трактовки подтверждает владелец. Отчёт: паспорт, нарезка, проверка каждого требования и набора, сводка, итог.</div></div>
${foot(6)}</section>`);

// 7 PROOF
S.push(`<section class="slide"><div class="kick">Как решаем · доказательство</div><h2>Не мнение модели, а расчёт, который можно повторить</h2>
<div style="display:grid;grid-template-columns:620px 1fr;gap:70px;margin-top:34px">
<img src="img/calc.png" style="width:600px;border:3px solid var(--ink);border-radius:24px;box-shadow:6px 9px 0 var(--ink);transform:rotate(-1.5deg)">
<div>${[["Один вход, два итога", "Правило А, строка 38 → 0 ₸. Правило Б, строка 64 → 4&nbsp;000 ₸"], ["Контрольный пример", "Покупку совершили и провели внутри одного месяца: оба правила дают 4&nbsp;000 ₸. Спор только о стыке месяцев"], ["Повторит любой", "Команда replay пересчитывает пример без модели и ключа"]]
 .map(([h, t], k) => `<div style="display:flex;gap:22px;margin-bottom:34px"><div class="num" style="flex:none;display:grid;place-items:center;width:62px;height:62px;border-radius:50%;background:${k ? "var(--ink)" : "var(--orange)"};color:#fff;font-size:30px">${k + 1}</div><div><div style="font-size:30px;font-weight:760">${h}</div><div style="font-size:22px;line-height:1.35;margin-top:6px">${t}</div></div></div>`).join("")}
<div class="card" style="background:var(--lav);padding:22px;font-size:21px"><b>Тем же методом найден второй конфликт:</b> Ф-3 и Ф-4 дают 1 или 2 push за месяц после смены порога</div></div></div>
${foot(7)}</section>`);

// 8 CLARIFY
S.push(`<section class="slide o"><div class="sun" style="width:640px;height:640px;right:-160px;bottom:-280px"></div>
<div class="kick">Как решаем · уточнение</div><h2>Вопрос тому, кто может принять решение</h2>
<div style="display:grid;grid-template-columns:1fr 720px;gap:50px;margin-top:40px">
<div style="display:flex;flex-direction:column;gap:18px">
${[["Агент → владелец", "По какой дате относить покупку к месяцу: совершения или проведения?", "var(--panel)"], ["Владелец", "Своими словами: «отдельный счётчик “Переходящие”»", "var(--yellow)"],
   ["Агент", "Довопрос: сколько получат январь, февраль и счётчик «Переходящие»?", "var(--panel)"], ["Ошибочная редакция", "Покупка внутри месяца учтена дважды. Замечание о месяце осталось открытым", "var(--lav)"]]
 .map(([w, t, bg], k) => `<div class="bubble" style="background:${bg};${k % 2 ? "margin-left:60px" : "margin-right:60px"}"><div class="tag muted" style="margin-bottom:6px">${w}</div><div style="font-size:22px;font-weight:600;line-height:1.3">${t}</div></div>`).join("")}
</div>
<div><img src="img/question.png" style="width:720px;border:3px solid var(--ink);border-radius:24px;box-shadow:6px 9px 0 var(--ink);transform:rotate(1.5deg)">
<div style="display:flex;align-items:center;gap:22px;margin-top:40px">${face("sm")}<div><div class="num" style="font-size:64px">30 с</div><div style="font-size:21px;font-weight:600">перепроверка ошибочной редакции: двойной учёт найден</div></div></div></div></div>
${foot(8)}</section>`);

// 9 PROJECT IN BRIEF: architecture
const box = (t, s, bg = "var(--panel)", col = "var(--ink)") => `<div class="card" style="background:${bg};color:${col};padding:20px 22px;box-shadow:4px 6px 0 var(--ink)"><div style="font-size:24px;font-weight:760">${t}</div><div style="font-size:18px;line-height:1.3;margin-top:6px">${s}</div></div>`;
S.push(`<section class="slide"><div class="kick">Проект коротко · архитектура</div><h2>LLM рассуждает, код проверяет</h2>
<div class="grid" style="grid-template-columns:1fr 1fr 1fr 1fr;gap:22px;margin-top:44px;align-items:stretch">
${box("Вход", "PDF / DOCX / MD / TXT → строки, таблицы с заголовками, JSON-примеры")}
${box("Сегментация", "Блоки с адресом строки, роль блока, исходные ID")}
${box("LLM · блоки", "Параллельно до 8 воркеров: реестр и оценка каждого требования", "var(--lav)")}
${box("LLM · весь набор", "Сверка правил, примеров и API: полнота, согласованность", "var(--lav)")}
</div>
<div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:22px;margin-top:26px">
${box("Валидатор · код", "Каждая цитата и номер строки сверяются с исходником. Невалидный ответ модели отклоняется", "var(--ink)", "var(--paper)")}
${box("Witness VM · код", "Две трактовки правила исполняются на одном входе и на контрольном. Разные итоги = конфликт", "var(--yellow)")}
${box("Приоритет и перепроверка", "Критичность по версионной шкале. Адресная перепроверка новой редакции")}
</div>
<div class="grid" style="grid-template-columns:1fr 1fr;gap:22px;margin-top:26px">
${box("Выход", "JSON → HTML / PDF на русском · веб-интерфейс · экспорт задач в Jira (CSV) · паспорт с коммитом и SHA-256", "var(--peach)")}
${box("Провайдеры", "Адаптеры Codex CLI (GPT-6.2 Astra), Gemini и Cloudflare Workers AI · при лимите квоты частичный отчёт", "var(--peach)")}
</div>
<div style="margin-top:30px;font-size:19px;font-weight:600" class="muted">261 проверка: 247 Python и 14 JS, включая 4 сквозных сценария в Chrome · запуск одной командой: <span style="font-family:Menlo,monospace;color:var(--ink)">python -m reviewer req.pdf report.html</span></div>
${foot(9)}</section>`);

// 10 RESULTS
S.push(`<section class="slide k"><div class="kick">Проект коротко · результат на официальном ТЗ</div><h2>Оба критических противоречия найдены и доказаны</h2>
<div class="grid" style="grid-template-columns:repeat(3,1fr);gap:26px;margin-top:50px">
${[["2 из 2", "критических конфликта исполнены: месяц покупки и повторный push", 1], ["16", "замечаний: 6 нарушений и 10 открытых вопросов, у каждого строка и цитата"], ["108", "записей реестра и 174 индивидуальные оценки по критериям"],
   ["5", "сценариев для QA: у 3 есть ожидаемый исход, 2 ждут решения владельца"], ["312 с", "полный прогон 198 строк, 19 вызовов модели"], ["Gemini", "бесплатная Flash-Lite нашла обе темы, но расчёты подтверждены только в прогоне Astra"]]
 .map(([n, t, hi]) => `<div class="card" style="background:${hi ? "var(--yellow)" : "#2b2530"};color:${hi ? "var(--ink)" : "var(--paper)"};border-color:var(--paper);box-shadow:5px 8px 0 var(--orange);padding:28px"><div class="num" style="font-size:76px">${n}</div><div style="font-size:21px;line-height:1.3;margin-top:12px">${t}</div></div>`).join("")}
</div>
<div style="margin-top:34px;font-size:18px;opacity:.7">Отчёт подачи: GPT-6.2 Astra через Codex · агент 8ab3b76 · runs/bcc-jury/review.json, SHA-256 68fafda6…</div>
${foot(10)}</section>`);

// 11 BUSINESS MODEL
S.push(`<section class="slide"><div class="kick">Бизнес-модель</div><h2>Кто платит и за что</h2>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr 380px;gap:24px;margin-top:40px;align-items:stretch">
${[["Пилот", "2,5 млн ₸", "≈ 125–208 тыс. ₸ за пару версий ТЗ", ["До него: встреча на 2 обезличенных ТЗ", "12–20 пар версий, 2 команды", "Итог: время, ложные блокеры, решение о покупке"], "var(--yellow)"],
   ["Команда · SaaS", "250 тыс. ₸", "в месяц за команду", ["До 10 пользователей", "До 100 прогонов", "Веб, PDF, экспорт в Jira"], "var(--peach)"],
   ["Банк · on-prem", "от 18 млн ₸", "в год + внедрение от 5 млн ₸", ["Контур банка, модель по выбору банка", "Безлимит команд", "SSO и поставка — план 2027"], "var(--lav)"]]
 .map(([n, p, per, l, bg], k) => `<div class="card ${k === 0 ? "tilt" : ""}" style="background:${bg};padding:26px"><div class="tag">${["Шаг 1", "Шаг 2", "Шаг 3"][k]}</div><div style="font-size:28px;font-weight:760;margin-top:8px">${n}</div><div class="num" style="font-size:44px;margin-top:16px;white-space:nowrap">${p}</div><div style="font-size:18px;font-weight:600" class="muted">${per}</div><ul class="b" style="margin-top:16px">${l.map(x => `<li>${x}</li>`).join("")}</ul></div>`).join("")}
<div style="display:flex;flex-direction:column;gap:16px">
${[["Покупатель", "Руководитель аналитики или QA карточного направления"], ["Пользователи", "Аналитик, QA, владелец продукта"], ["Каналы", "Предлагаем пилот BCC → референс при согласии банка → прямые продажи"], ["Отличие", "Две русские цитаты, расчёт на общей операции, контроль, перепроверка редакции"]]
 .map(([h, t]) => `<div><div class="tag" style="color:var(--orange)">${h}</div><div style="font-size:20px;line-height:1.3;margin-top:4px">${t}</div></div>`).join("")}
</div></div>
<div class="card" style="margin-top:34px;background:var(--ink);color:var(--paper);padding:22px 30px;font-size:23px"><b style="color:var(--yellow)">Цель пилота:</b> −25% времени до первого принятого вопроса · ≥ 80% блокеров, согласованных двумя экспертами · ≤ 1 ложного блокера на документ</div>
${foot(11)}</section>`);

// 12 ECONOMICS
S.push(`<section class="slide o"><div class="sun" style="width:600px;height:600px;right:-150px;top:-240px"></div>
<div class="kick">Бизнес-модель · экономика и рынок</div><h2>Когда банку выгодно: порог окупаемости</h2>
<div style="display:grid;grid-template-columns:720px 1fr;gap:50px;margin-top:40px">
<div class="card" style="padding:30px 34px"><div style="font-size:28px;font-weight:760">Выгода команды в месяц, тыс. ₸</div><div style="font-size:18px" class="muted">при разной экономии минут на одно ТЗ · 20 ТЗ в месяц · 7&nbsp;500 ₸/ч</div><div style="position:relative;display:flex;gap:16px;margin-top:10px"><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">75</div><div style="width:100%;height:56px;background:#e8dccb;border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">30 мин</div></div><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">150</div><div style="width:100%;height:112px;background:#e8dccb;border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">60 мин</div></div><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">225</div><div style="width:100%;height:168px;background:#e8dccb;border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">90 мин</div></div><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">250</div><div style="width:100%;height:187px;background:var(--yellow);border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">100 мин</div></div><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">300</div><div style="width:100%;height:225px;background:var(--green);border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">120 мин</div></div><div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:340px"><div style="font-size:17px;font-weight:700;margin-bottom:6px;position:relative;z-index:2;background:var(--panel);padding:0 6px">375</div><div style="width:100%;height:281px;background:var(--green);border:3px solid var(--ink);border-radius:12px 12px 0 0"></div><div style="font-size:17px;font-weight:700;margin-top:8px">150 мин</div></div><div style="position:absolute;left:-6px;right:-6px;top:122px;border-top:4px dashed var(--orange)"></div><div style="position:absolute;left:0;top:92px;font-size:18px;font-weight:800;color:#c43d1a">цена подписки — 250</div></div><div style="display:flex;align-items:center;gap:18px;margin-top:16px"><div class="num" style="font-size:52px;white-space:nowrap">100 мин</div><div style="font-size:19px;line-height:1.3">на ТЗ — точка безубыточности. Каждая предотвращённая переделка добавляет ≈ 120 тыс. ₸</div></div></div>
<div style="display:flex;flex-direction:column;gap:18px">
${[["≈ 75 ₸", "модель на прогон: Gemini Flash-Lite по платным ставкам; на флагманской модели до ≈ 800 ₸"], ["58–89%", "остаток после модели и хостинга, до затрат на поддержку и внедрение"], ["≈ 300 млн ₸", "сценарная ёмкость Казахстана: 23 БВУ, число команд — гипотеза"], ["60 млн ₸", "цель ARR за 24 месяца; воронки пока нет"]]
 .map(([n, t]) => `<div class="card" style="padding:18px 24px;display:flex;align-items:center;gap:22px"><div class="num" style="font-size:44px;min-width:250px">${n}</div><div style="font-size:19px;line-height:1.3">${t}</div></div>`).join("")}
</div></div>
<div style="margin-top:26px;font-size:16px;font-weight:600">Все цифры — модель для проверки в пилоте. Допущения и пересчёт: docs/business-model.md</div>
${foot(12)}</section>`);

// 13 PLANS
const rm = [["Декабрь 2026", "Предлагаем пилот BCC", "Встреча на 2 обезличенных ТЗ, затем замер времени и ложных тревог", "var(--yellow)"],
 ["I кв. 2027", "Интеграции", "Экспорт в Jira (CSV) уже есть. Нативные Jira и Confluence, шаблон банка — при приёмке пилота", "var(--panel)"],
 ["II кв. 2027", "Контур банка", "On-prem и SSO после согласования ИБ, модель по выбору банка", "var(--panel)"],
 ["II пол. 2027", "Масштаб", "Новые банки и финтех при подтверждённом спросе", "var(--lav)"]];
S.push(`<section class="slide"><div class="kick">Дальнейшие планы</div><h2>Что дальше: от пилота к продукту для банков</h2>
<div style="position:relative;margin-top:80px">
<div style="position:absolute;left:0;right:0;top:34px;border-top:5px dashed var(--ink)"></div>
<div class="grid" style="grid-template-columns:repeat(4,1fr);gap:28px">
${rm.map(([d, h, t, bg], k) => `<div><div style="width:70px;height:70px;border-radius:50%;background:${k ? "var(--panel)" : "var(--orange)"};border:4px solid var(--ink);display:grid;place-items:center;font-weight:800;font-size:28px;position:relative">${k + 1}</div>
<div class="card ${k % 2 ? "tilt2" : "tilt"}" style="margin-top:30px;background:${bg};padding:26px;min-height:270px"><div class="pill">${d}</div><div style="font-size:30px;font-weight:760;margin:18px 0 10px">${h}</div><div style="font-size:20px;line-height:1.35">${t}</div></div></div>`).join("")}
</div></div>
<div style="display:flex;gap:16px;margin-top:50px;flex-wrap:wrap">${["Риск: ТЗ нельзя в облако → on-prem после согласования ИБ", "Риск: ложные тревоги → конфликт только с расчётом", "Риск: один провайдер → уже 3 адаптера"].map(ref).join("")}</div>
${foot(13)}</section>`);

// 14 CLOSING
(async () => {
  const qr = await QR.toDataURL("https://razbor-tz.onrender.com/", { margin: 1, width: 600, color: { dark: "#19161d", light: "#fffcf4" } });
  const qr2 = await QR.toDataURL("https://razbor-tz.onrender.com/reports/demo/review.html#execution-D-01", { margin: 1, width: 600, color: { dark: "#19161d", light: "#fffcf4" } });
  S.push(`<section class="slide k"><div class="sun" style="width:760px;height:760px;left:-260px;bottom:-380px"></div>
<div class="kick">Что просим · спасибо</div>
<h1 style="font-size:116px;margin-top:10px;color:var(--paper)">Вопросы?</h1>
<p class="lead" style="font-size:26px;max-width:600px;margin-top:24px;color:var(--paper)">Дайте нам два обезличенных ТЗ с правками и аналитика, который оценит вопросы агента. А пока попробуйте сами, без регистрации.</p>
<div style="position:absolute;left:90px;bottom:120px">${face("big")}</div>
<div style="position:absolute;right:90px;top:150px;display:flex;gap:36px">
 <a class="card tilt" href="https://razbor-tz.onrender.com/" style="display:block;text-decoration:none;color:var(--ink);padding:22px;text-align:center;width:320px"><img src="${qr}" style="width:270px;height:270px;display:block;margin:0 auto"><div style="font-size:26px;font-weight:780;margin-top:16px">Проверить своё ТЗ</div><div style="font-size:18px;margin-top:6px" class="muted">razbor-tz.onrender.com</div></a>
 <a class="card tilt2" href="https://razbor-tz.onrender.com/reports/demo/review.html#execution-D-01" style="display:block;text-decoration:none;color:var(--ink);padding:22px;text-align:center;width:320px;background:var(--yellow)"><img src="${qr2}" style="width:270px;height:270px;display:block;margin:0 auto"><div style="font-size:23px;font-weight:780;margin-top:16px;white-space:nowrap">Отчёт: 0 или 4&nbsp;000&nbsp;₸</div><div style="font-size:18px;margin-top:6px">расчёт по официальному ТЗ</div></a>
</div>
<div style="position:absolute;right:90px;bottom:110px;display:flex;gap:18px"><a class="btn" style="text-decoration:none" href="https://razbor-tz.onrender.com/">Открыть сайт ↗</a><a class="btn" style="background:var(--orange);color:var(--ink);text-decoration:none" href="https://github.com/gojosatorukz/bcc-requirements-reviewer">Код на GitHub ↗</a></div>
${foot(14)}</section>`);
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Разбор ТЗ — питч</title><style>${css}</style></head><body>${S.join("\n")}</body></html>`;
  fs.writeFileSync(path.join(OUT, "index.html"), html);
  console.log("slides", S.length);
})();
