"""Self-contained Russian report for full-document review results (schema 2)."""

from collections import Counter, defaultdict
from pathlib import Path

from .contract import source_lines, validate_review, validate_shape
from .full import BATCH, RANKED_WHOLE, WHOLE, check_batch, check_critical_sources, check_whole
from .json_examples import check_findings, inspect_examples
from . import priority
from .render import render_conflict, render_passport, render_source, safe
from .segments import segment


def validate_report(report):
    lines = source_lines(report)
    raw_lines = [lines.get(i, "") for i in range(1, report["run"]["input_lines"] + 1)]
    expected = segment(raw_lines)
    segments, registry, whole = report["segments"], report["registry"], report["whole"]
    if "json_checks" in report:
        checks = inspect_examples(raw_lines, expected)
        if checks != report["json_checks"]:
            raise ValueError("Результат проверки JSON расходится с исходником")
        check_findings(checks, whole["findings"])
    if len(segments) != len(expected):
        raise ValueError("Число исходных блоков изменилось")
    records = {r["id"]: r for r in registry}
    if list(records) != [f"R-{i+1:03d}" for i in range(len(registry))]:
        raise ValueError("Неверная нумерация реестра")
    seen = []
    for item, base in zip(segments, expected):
        if any(item.get(k) != v for k, v in base.items()):
            raise ValueError("Блок не совпадает с исходным текстом")
        entries = []
        for rid in item["requirements"]:
            if rid not in records or records[rid]["start"] != base["start"]:
                raise ValueError("Нарушена привязка требования к блоку")
            entries.append({k:v for k, v in records[rid].items() if k not in {"id","original_id","start"}})
        seen.extend(item["requirements"])
        batch = {"rows": [{"start": item["start"], "items": entries, "note": item["note"]}]}
        validate_shape(batch, BATCH, "registry")
        check_batch(batch, [base], lines)
    if seen != list(records):
        raise ValueError("Реестр пропускает или дублирует пункты")
    validate_review({"conflicts": report["conflicts"]}, lines)
    clean = dict(whole, findings=[{k:v for k,v in f.items() if k != "id"} for f in whole["findings"]])
    policy = report.get("priority_policy")
    if policy is not None and policy != priority.POLICY:
        raise ValueError("Неизвестная версия шкалы приоритетов")
    validate_shape(clean, RANKED_WHOLE if policy else WHOLE, "whole")
    check_whole(whole, registry, report["conflicts"], lines)
    if policy:
        check_critical_sources(whole, report["conflicts"])
        for f in whole["findings"]:
            if priority.derive_priority(f, lines) != f["priority"]:
                raise ValueError(f"{f['id']}: приоритет расходится с правилом {policy}")
    for i, f in enumerate(whole["findings"], 1):
        if f["id"] != f"D-{i:02d}":
            raise ValueError("Неверная нумерация замечаний")
    return lines


def link(anchor, label):
    return f'<a href="#{safe(anchor)}">{safe(label)}</a>'


def line_link(n):
    return link(f"source-line-{n}", f"строка {n}")


def evidence(refs):
    return "".join(f'<blockquote>{safe(r["quote"])} {line_link(r["line"])}</blockquote>' for r in refs)


def render_json_checks(checks):
    examples = checks.get("examples", [])
    if not examples:
        return ""
    rows = []
    for item in examples:
        status = {"valid": "Корректный JSON", "invalid": "Ошибка синтаксиса",
                  "unchecked": "Проверка не завершена"}[item["status"]]
        detail = ""
        if item["status"] != "valid":
            detail = f' · {safe(item["diagnostic"])} · {line_link(item["line"])}'
        rows.append(f'<li>{line_link(item["start"])} · <strong>{status}</strong>{detail}</li>')
    return ('<section id="json-syntax"><h2>Синтаксис JSON-примеров</h2>'
            '<p>Парсер проверяет каждый блок с меткой JSON. Поля, типы и поведение API оцениваются в реестре требований.</p>'
            '<ul>' + "".join(rows) + '</ul></section>')


def render_decision_drafts(finding, candidates):
    if finding["priority"] != "критический" or finding["category"] != "нарушение":
        return ""
    cited_lines = {ref["line"] for ref in finding["evidence"]}
    scenarios = []
    for number in finding["candidates"]:
        candidate = candidates[number - 1]
        outcomes = (candidate["first"], candidate["second"])
        if any(outcome["source"]["line"] not in cited_lines for outcome in outcomes):
            continue
        group = f'draft-{finding["id"]}-{number}'
        scenario = safe(candidate["scenario"])
        options, drafts = [], []
        for index, outcome in enumerate(outcomes, 1):
            choice = f"{group}-{index}"
            expected = safe(outcome["outcome"])
            source = line_link(outcome["source"]["line"])
            options.append(f'''<input type="radio" id="{choice}" name="{group}" value="{index}" autocomplete="off">
<label for="{choice}"><b>Исход {index}</b><span>{expected}</span></label>
<span class="draft-source">{source}</span>''')
            drafts.append(f'''<div class="draft-result draft-result-{index}">
<h4>Черновик критерия приёмки для QA</h4>
<p><b>Сценарий.</b> {scenario}</p>
<p><b>Ожидаемый результат.</b> {expected}</p>
<p><b>Контрольный пример.</b> {safe(candidate['agreement_control'])}</p>
<p><b>Основание.</b> {source}</p></div>''')
        scenarios.append(f'''<fieldset class="draft-scenario"><legend>{safe(candidate['title'])}</legend>
<p><b>Сценарий.</b> {scenario}</p>
<p>Выберите ожидаемый результат для черновика.</p>
<div class="draft-options">{''.join(options)}{''.join(drafts)}</div></fieldset>''')
    if not scenarios:
        return ""
    return f'''<details class="decision-draft"><summary>Подготовить критерий приёмки для QA</summary>
{''.join(scenarios)}</details>'''


def render_full(report):
    lines = validate_report(report)
    run, segments, registry, whole = (report[k] for k in ("run", "segments", "registry", "whole"))
    title = next((s["section"] for s in segments if s["kind"] == "заголовок"), run["input_name"])
    defects = [f for f in whole["findings"] if f["category"] == "нарушение"]
    critical = [f for f in defects if f["priority"] == "критический"]
    questions = len(whole["findings"]) - len(defects)
    roles = Counter(r["role"] for r in registry)
    forms = Counter(s["kind"] for s in segments)
    backlinks = defaultdict(list)
    related = defaultdict(list)
    decisions = {}
    findings = []
    for f in whole["findings"]:
        fid = f["id"]
        for rid in f["requirements"]:
            related[rid].append(link(fid, fid))
        for cid in f["candidates"]:
            decisions[cid] = link(fid, fid + " · " + f["category"])
        refs = " · ".join(link(rid, rid) for rid in f["requirements"])
        for src in f["evidence"]:
            backlinks[src["line"]].append((fid, fid))
        basis = ""
        if "triage" in f:
            triage = f["triage"]
            proof = []
            for case in triage["witness"]:
                proof.append(f'<p>{safe(case["scenario"])}</p>')
                for side in (case["first"], case["second"]):
                    proof.append(f'<p>{safe(side["outcome"])} · {line_link(side["source"]["line"])}</p>')
            basis = (f'<details><summary>Основание приоритета · {safe(report["priority_policy"])}</summary>'
                     f'<p>{safe(priority.RULES[triage["rule"]][1])}</p>{"".join(proof)}</details>')
        findings.append(f'''<article id="{fid}" class="finding"><small>{fid} · {safe(f['category'])} · {safe(f['priority'])}</small>
<h3>{safe(f['title'])}</h3><p>{safe(f['criterion'])}. {safe(f['reason'])}</p><p><b>Последствие.</b> {safe(f['impact'])}</p>
{evidence(f['evidence'])}<p><b>{safe(f['owner'])}.</b> {safe(f['question'])}</p>
<p><b>Исправление.</b> {safe(f['action'])}</p><p>{refs}</p>
{basis}{render_decision_drafts(f, report['conflicts'])}</article>''')
    closed = []
    for i, item in enumerate(whole["reconciled"], 1):
        key = f"resolution-{i}"
        for cid in item["candidates"]:
            decisions[cid] = link(key, "Проверено альтернативное прочтение")
        for rid in item["requirements"]:
            related[rid].append(link(key, "Пояснение"))
        closed.append(f'<li id="{key}">{safe(item["reason"])} {evidence(item["evidence"])}</li>')
    scenario_parts = []
    for i, candidate in enumerate(report["conflicts"], 1):
        scenario_parts.append(f'<p>Результат итоговой проверки · {decisions[i]}</p>'
                              + render_conflict(i, candidate, lines, backlinks))
    blocks_by_start = {s["start"]: s for s in segments}
    rows = []
    for item in registry:
        rid = item["id"]
        cite = item["evidence"]
        backlinks[cite["line"]].append((rid, rid))
        section = safe(blocks_by_start[item["start"]]["section"].removeprefix(title + " / "))
        rows.append(f'''<tr id="{rid}"><td><b>{rid}</b><br>{safe(item['original_id'])}</td>
<td><small>{safe(item['role'])} · {section}</small><br><b>{safe(item['statement'])}</b>
<blockquote>{safe(cite['quote'])} {line_link(cite['line'])}</blockquote></td>
<td><small>{safe(item['status'])}</small><br>{safe(item['assessment'])}<p>{' · '.join(related[rid])}</p></td></tr>''')
    routing = []
    for b in segments:
        address = line_link(b["start"])
        if b["end"] != b["start"]:
            address += "–" + str(b["end"])
        header = " · шапка " + line_link(b["header"]) if "header" in b else ""
        targets = " · ".join(link(r, r) for r in b["requirements"])
        routing.append(f'<tr><td>{address}</td><td>{safe(b["kind"])}{header}</td><td>{safe(b["note"])} {targets}</td></tr>')
    verdicts = []
    for key, label in (("development", "Разработка"), ("testing", "Тестирование")):
        g = whole[key]
        verdicts.append(f'<article><small>{label}</small><h3>{safe(g["status"].capitalize())}</h3>'
                        f'<p>{safe(g["rationale"])}</p>{" · ".join(line_link(e["line"]) for e in g["evidence"])}</article>')
    axes = "".join(f'<article class="finding"><h3>{safe(d["criterion"].capitalize())}</h3><p>{safe(d["assessment"])}</p>'
                   f'{evidence(d["evidence"])}</article>' for d in whole["dimensions"])
    nav = " · ".join(link(k,v) for k,v in [("actions","Замечания"),("register","Реестр"),("quality","Оценка набора"),
                                            ("coverage","Покрытие"),("source","Исходник"),("passport","Паспорт")])
    summary = (f'{roles["требование"]} требований · {roles["приёмочный пример"]} приёмочных примеров · '
               f'{roles["пример контракта"]} примеров контракта')
    crit_list = "".join(f'<li>{link(f["id"],f["id"]+" · "+f["title"])}</li>' for f in critical)
    styles = Path(__file__).with_name("render.css").read_text(encoding="utf-8")
    styles += """
.full nav{margin:1rem 0}.metrics,.gates{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13em),1fr));gap:.8rem;margin:1rem 0}
.metrics>div,.gates>article{padding:1rem;background:var(--green);border:1px solid var(--line)}
.metrics strong{font-size:1.65rem;display:block}.full h3{font-size:1.05rem;margin:.35rem 0}
.full section{margin:1.6rem 0}.finding{border-top:1px solid var(--line);padding:1rem 0}
.finding p+p{margin-top:.45rem}.full small{color:var(--muted)}.full blockquote{margin:.45rem 0}
.full table{width:100%;border-collapse:collapse}.full td,.full th{padding:.55rem;vertical-align:top;border-bottom:1px solid var(--line);text-align:left}
.full th{background:var(--green)}.full summary{cursor:pointer}.full tr:target,.finding:target{background:var(--warm)}
.full .scroll{overflow:auto}.full td:first-child{min-width:4.6em}.full section>h2{border-top:2px solid var(--deep);padding-top:1rem}
.decision-draft{margin-top:.75rem;padding:.65rem .85rem;border:1px solid var(--line);background:var(--green)}
.decision-draft summary{font-weight:650;color:var(--deep)}
.draft-scenario{min-inline-size:0;margin:.8rem 0 0;padding:.65rem;border:1px solid var(--line);background:var(--paper)}
.draft-scenario legend{padding:0 .3rem;font-weight:650}
.draft-options{display:grid;grid-template-columns:auto minmax(0,1fr) max-content;gap:.35rem .6rem;margin-top:.45rem;align-items:start}
.draft-options input{margin:.65rem 0 0;accent-color:var(--deep);cursor:pointer}
.draft-options label{display:block;padding:.25rem .4rem;border-radius:3px;cursor:pointer}
.draft-options label span{display:block}
.draft-options input:checked + label{background:var(--green)}
.draft-source a{display:inline-block;padding:.4rem 0;font-size:.875rem}
.draft-result{display:none;grid-column:1/-1;padding:.75rem;border-left:3px solid var(--deep);background:white}
.draft-result h4{margin:0 0 .35rem}
.draft-options input[value="1"]:checked ~ .draft-result-1,.draft-options input[value="2"]:checked ~ .draft-result-2{display:block}
@media(max-width:650px){.full .registry td{display:block;border:0}.full .registry tr{display:block;border-bottom:1px solid var(--line)}.registry thead{display:none}}
@page{size:A4;margin:15mm 15mm 18mm;@bottom-right{content:counter(page) " / " counter(pages);font:9pt sans-serif;color:#52625b}}
@media print{
 html{font-size:12px}body,main,.full{font-family:Arial,sans-serif;background:white}
 header,nav,.skip-link,.backlinks,.decision-draft{display:none}
 main.full{width:auto;margin:0;padding:0;border:0;font-size:10pt;line-height:1.35}
 .full h1{font:600 23pt Arial,sans-serif}.full h2{font-size:15pt}.full h3{font-size:11pt}
 .full h2,.full h3{break-after:avoid}.full section{margin:9pt 0}.full section>h2{padding-top:7pt}
 .metrics{grid-template-columns:repeat(3,1fr)}.gates{grid-template-columns:repeat(2,1fr)}
 .full .registry{table-layout:fixed}.full .registry th:first-child{width:10%}.full .registry th:nth-child(2){width:49%}
 .full .registry thead{display:table-header-group}.full .registry tr{display:table-row}.full .registry td{display:table-cell}
 .full td,.full th{padding:4pt}.full .registry td:first-child{white-space:nowrap}
 .full .source-row,.full tr,.full blockquote{break-inside:avoid}.full .source-text{font:8.5pt/1.3 Arial,sans-serif}
 .full .finding{padding:6pt 0}.full small{font-size:8pt}.full blockquote{margin:2pt 0}
 .full .facts{display:flex;flex-wrap:wrap;gap:3pt 9pt}.full .facts div{padding:2pt 0}.full .facts dd{margin:1pt 0}
 .full .attempts li{break-inside:avoid}.full .attempts .facts div{border:0}
}
"""
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>{safe(title)} · Ревью требований</title><style>{styles}</style></head>
<body><a class="skip-link" href="#report">К результатам</a><header class="masthead"><div class="masthead-inner"><strong>Разбор ТЗ</strong><span>Отчёт полного прогона</span></div></header>
<main class="full" id="report"><small>{safe(run['input_name'])} · {safe(run['started_utc'][:10])} · {safe(run['model'])}</small>
<h1>{safe(title)}</h1><nav>{nav}</nav>
<div class="metrics"><div><strong>{len(registry)}</strong>пунктов в реестре</div><div><strong>{len(defects)}</strong>нарушений, включая {len(critical)} критических</div><div><strong>{questions}</strong>открытых вопросов</div></div>
<p>{summary}.</p><div class="gates">{''.join(verdicts)}</div>
<h2>Критические дефекты</h2>{'<ol>'+crit_list+'</ol>' if critical else '<p>Критические дефекты не выявлены.</p>'}
<section id="actions"><h2>Что исправить в первую очередь</h2><p>Замечания в порядке приоритета. У каждого есть адресат, основание и предлагаемое изменение.</p>
{''.join(findings) or '<p>Замечаний не выявлено.</p>'}</section>
<section id="quality"><h2>Качество набора требований</h2>{axes}</section>
{render_json_checks(report.get("json_checks", {}))}
<section id="register"><h2>Реестр и проверка каждого пункта</h2><p>Единица учёта — отдельно проверяемое обязательство или пример. Один исходный номер может соответствовать нескольким пунктам. Повторные утверждения из разных разделов остаются прослеживаемыми. Локальная оценка сопровождается ссылками на итоговые замечания или пояснения.</p>
<table class="registry"><thead><tr><th>ID отчёта / исходника</th><th>Формулировка и цитата</th><th>Проверка пункта</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><h2>Проверка альтернативных прочтений</h2><p>Сценариев-кандидатов: {len(scenario_parts)}. У каждого указано решение итогового ревью.</p>
{''.join(scenario_parts)}{('<h3>Согласованные прочтения</h3><ol>'+''.join(closed)+'</ol>') if closed else ''}</section>
<section id="coverage"><h2>Карта покрытия исходника</h2><p>Учтено {len(lines)} непустых строк, {len(segments)} блоков, {forms['шапка таблицы']} таблиц, {forms['строка таблицы']} строк данных таблиц и {forms['пример json']} блоков JSON. Структура, шаблоны и контекст имеют отдельные записи.</p>
<details><summary>Открыть все блоки и основания классификации</summary><table><thead><tr><th>Источник</th><th>Тип</th><th>Назначение и пункты</th></tr></thead><tbody>{''.join(routing)}</tbody></table></details></section>
<section id="source"><h2>Текст исходного документа</h2><ol class="source-list">{render_source(lines,backlinks,run.get("pages", []))}</ol></section>{render_passport(run,2)}</main></body></html>
'''
