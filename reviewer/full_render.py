"""Self-contained Russian report for full-document review results (schema 2)."""

import json
import re
from base64 import b64encode
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from .contract import source_lines, validate_review, validate_shape
from .executable import unexecuted_message
from .execution_view import acceptance_lines, first_step_comparisons, quantity, render_executions
from .full import BATCH, QUALITY_BATCH, RANKED_WHOLE, WHOLE, check_batch, check_critical_sources, check_whole
from .json_examples import check_findings, verify_saved_checks
from .jira_export import issues_csv
from .example_shapes import object_shapes
from . import priority
from .render import render_conflict, render_passport, render_source, safe
from .segments import segment
from . import response_fields


PRINT_SCRIPT = """const printButton = document.getElementById('print-report');
printButton.hidden = false;
printButton.addEventListener('click', () => window.print());
let openedForPrint = [];
window.addEventListener('beforeprint', () => {
  for (const detail of document.querySelectorAll('main details:not([open])')) {
    openedForPrint.push(detail);
    detail.open = true;
  }
});
window.addEventListener('afterprint', () => {
  for (const detail of openedForPrint) detail.open = false;
  openedForPrint = [];
});"""
PRINT_HASH = b64encode(sha256(PRINT_SCRIPT.encode("utf-8")).digest()).decode("ascii")


def validate_report(report):
    lines = source_lines(report)
    if "lifecycle_checks" in report:
        from .lifecycle import validate_saved
        validate_saved(report["lifecycle_checks"], lines)
    raw_lines = [lines.get(i, "") for i in range(1, report["run"]["input_lines"] + 1)]
    expected = segment(raw_lines, report["run"].get("pdf_json_blocks", ()))
    segments, registry, whole = report["segments"], report["registry"], report["whole"]
    if "json_checks" in report:
        checks = verify_saved_checks(report["json_checks"], raw_lines, expected)
        check_findings(checks, whole["findings"])
    if "example_shapes" in report:
        if ("json_checks" not in report or
                json.dumps(report["example_shapes"], sort_keys=True) !=
                json.dumps(object_shapes(raw_lines, expected, checks), sort_keys=True)):
            raise ValueError("Поля JSON-примеров расходятся с исходным документом")
    if "response_field_review" in report:
        field_review = report["response_field_review"]
        shapes = report.get("example_shapes", ())
        if isinstance(field_review, dict) and field_review.get("version") == 2:
            if "json_checks" not in report:
                raise ValueError("Отсутствуют исходные JSON-примеры оценки полей")
            shapes = object_shapes(raw_lines, expected, checks, complete=True)
        groups = response_fields.response_groups(lines, shapes, expected)
        response_fields.validate_saved(field_review, groups, whole["findings"], lines)
    if len(segments) != len(expected):
        raise ValueError("Число исходных блоков изменилось")
    records = {r["id"]: r for r in registry}
    if list(records) != [f"R-{i+1:03d}" for i in range(len(registry))]:
        raise ValueError("Неверная нумерация реестра")
    item_schema = BATCH
    if "item_quality_version" in report:
        if type(report["item_quality_version"]) is not int or report["item_quality_version"] != 1:
            raise ValueError("Неизвестная версия оценки отдельных требований")
        item_schema = QUALITY_BATCH
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
        validate_shape(batch, item_schema, "registry")
        check_batch(batch, [base], lines)
    if seen != list(records):
        raise ValueError("Реестр пропускает или дублирует пункты")
    http_status_policy = report.get("http_status_policy")
    validate_review({"conflicts": report["conflicts"]}, lines, http_status_policy=http_status_policy)
    clean = dict(whole, findings=[{k:v for k,v in f.items() if k != "id"} for f in whole["findings"]])
    policy = report.get("priority_policy")
    if policy is not None and policy != priority.POLICY:
        raise ValueError("Неизвестная версия шкалы приоритетов")
    validate_shape(clean, RANKED_WHOLE if policy else WHOLE, "whole")
    check_whole(whole, registry, report["conflicts"], lines, http_status_policy=http_status_policy)
    if policy:
        check_critical_sources(whole, report["conflicts"])
        for f in whole["findings"]:
            if priority.derive_priority(f, lines) != f["priority"]:
                raise ValueError(f"{f['id']}: приоритет расходится с правилом {policy}")
    for i, f in enumerate(whole["findings"], 1):
        if f["id"] != f"D-{i:02d}":
            raise ValueError("Неверная нумерация замечаний")
    if "executables" in report:
        from .executable import validate_saved
        validate_saved(report, lines)
    return lines


def link(anchor, label):
    return f'<a href="#{safe(anchor)}">{safe(label)}</a>'


def line_link(n):
    return link(f"source-line-{n}", f"строка {n}")


def evidence(refs):
    return "".join(f'<blockquote>{safe(r["quote"])} {line_link(r["line"])}</blockquote>' for r in refs)


QA_AXES = {"initial_action": "Первое действие", "retry_effect": "Повтор действия",
           "reentry": "Повторное наступление условия", "boundary": "Граница значения",
           "isolation": "Независимость объектов"}


def render_lifecycle(checks, backlinks, skipped=()):
    cards = []
    for index, check in enumerate(checks, 1):
        anchor = f"qa-scenario-{index}"
        label = QA_AXES[check["axis"]]
        needs_decision = check["status"] == "needs_decision"
        observation_label = "Что следует из ТЗ" if needs_decision else "Ожидаемый результат"
        question = (f'<p><b>Вопрос владельцу.</b> {safe(check["question"])}</p>'
                    if needs_decision else "")
        for ref in check["evidence"]:
            backlinks[ref["line"]].append((anchor, f"QA · {label}"))
        cards.append(f'''<article class="finding qa-scenario" id="{anchor}" tabindex="-1">
<h3>{label}</h3><small>{"Нужно решение владельца" if needs_decision else "Результат следует из ТЗ"}</small>
<p><b>Условия.</b> {safe(check['situation'])}</p><p><b>Действие.</b> {safe(check['action'])}</p>
<p><b>{observation_label}.</b> {safe(check['observation'])}</p>{question}{evidence(check['evidence'])}</article>''')
    if skipped:
        reasons = "".join(f'<li><b>{QA_AXES[item["axis"]]}.</b> {safe(item["reason"])}</li>' for item in skipped)
        if not cards:
            cards.append('<p>Для рассмотренных ситуаций отдельные сценарии неприменимы.</p>')
        cards.append(f'<details id="qa-skipped"><summary>Почему пропущены ситуации · {len(skipped)}</summary>'
                     f'<ul>{reasons}</ul></details>')
    return ('<section id="qa-scenarios"><h2>Сценарии для QA</h2>' + "".join(cards) + '</section>') if cards else ""


def markdown_text(text):
    # Keep model-supplied text on one line so it cannot introduce Markdown blocks.
    line = " ".join(str(text).splitlines()).strip()
    line = re.sub(r"([\\`*_~\[\]<>|&:@#])", r"\\\1", line)
    return re.sub(r"^(\d{1,9})?([+.()=\-])", r"\1\\\2", line)


def execution_markdown(item):
    if item["status"] != "executed":
        if item.get("acceptance_example"):
            lines = iter(acceptance_lines(item))
            return "## " + markdown_text(next(lines)) + "\n\n" + "\n\n".join(map(markdown_text, lines))
        title, reason = unexecuted_message(item)
        return f"## {title}\n\n" + markdown_text(reason)

    program, result = item["program"], item["result"]
    fields = {f["key"]: f for f in program["states"] + program["inputs"]}

    def value(key, n):
        if isinstance(n, str):
            # Preserve the literal API value, including underscores or backticks.
            fence = "`" * (1 + max((len(s) for s in re.findall(r"`+", n)), default=0))
            padding = " " if n.startswith("`") or n.endswith("`") else ""
            return fence + padding + n + padding + fence
        unit = fields[key]["unit"]
        digits = format(n, ",").replace(",", "\u202f").replace("-", "−")
        return digits if unit == "1" else f"{digits} {markdown_text(unit)}"

    def values(mapping):
        return ", ".join(f'{markdown_text(fields[k]["label"])} {value(k, n)}' for k, n in mapping.items())

    def scenario(name):
        trace = result[name]
        start = {row["key"]: row["value"] for row in program[name]["initial"]}
        lines = [f"Перед первым событием · {values(start)}"]
        for step in trace["steps"]:
            event = f'{step["step"]}. {markdown_text(step["label"])}'
            lines.append(event + (f' · {values(step["inputs"])}' if step["inputs"] else ""))
        for side, letter in (("first", "А"), ("second", "Б")):
            line = program[side]["source"]["line"]
            lines.append(f"Итог по правилу {letter} (строка {line}) · {values(trace['final'][side])}")
        return "\n\n".join(lines)

    diff = result["scenario"]["first_difference"]
    observations = [f'{markdown_text(fields[key]["label"])}. Правило А даёт {value(key, first)}, '
                    f'правило Б даёт {value(key, second)}.'
                    for key, first, second in first_step_comparisons(result["scenario"])]
    divergence = f'Первое расхождение на шаге {diff["step"]} · ' + "\n\n".join(observations)
    return ("## Исполняемый пример\n\n" + divergence + "\n\n" + scenario("scenario")
            + "\n\n### Контрольный пример\n\nЗначения совпали на каждом шаге.\n\n" + scenario("control")
            + "\n\nОжидаемый исход для приёмки предстоит согласовать с владельцем ТЗ.")


def finding_markdown(finding, run, page_by_line, execution=None):
    def field(key):
        return markdown_text(finding[key])

    sections = [
        f"# {field('id')} · {field('title')}",
        (f"**Тип:** {field('category')}  \n**Приоритет:** {field('priority')}  \n"
         f"**Адресат:** {field('owner')}  \n**Статус карточки:** черновик для согласования"),
        (f"**Документ:** {markdown_text(run['input_name'])}  \n"
         f"**SHA-256 исходника:** {markdown_text(run['input_sha256'])}"),
        f"## Основание\n\n{field('reason')}",
        f"## Вопрос владельцу\n\n{field('question')}",
        f"## Предлагаемое действие\n\n{field('action')}",
    ]
    if execution:
        sections.append(execution_markdown(execution))
    sections.append("## Цитаты из исходника")
    for ref in finding["evidence"]:
        line, quote = ref["line"], ref["quote"]
        page = f", страница {page_by_line[line]}" if line in page_by_line else ""
        fence = "`" * max(3, 1 + max((len(ticks) for ticks in re.findall(r"`+", quote)), default=0))
        sections.append(f"Строка {line}{page}\n\n{fence}text\n{quote}\n{fence}")
    if not finding["evidence"]:
        sections.append("Цитаты в отчёте отсутствуют.")
    return "\n\n".join(sections) + "\n"


def finding_download(finding, run, page_by_line, execution=None):
    payload = b64encode(finding_markdown(finding, run, page_by_line, execution).encode("utf-8")).decode("ascii")
    fid = safe(finding["id"])
    return (f'<a class="finding-export" download="{fid}.md" aria-label="Скачать карточку {fid} в Markdown" '
            f'href="data:text/markdown;charset=utf-8;base64,{payload}">Скачать карточку (.md)</a>')


def decision_brief(report):
    items = {row["finding_id"]: row for row in report.get("executables", {}).get("items", [])}
    findings = sorted(report["whole"]["findings"], key=lambda f: priority.ORDER[f["priority"]])
    if not findings:
        return '<p class="brief-empty">По результатам этого прогона замечаний для согласования нет.</p>'
    cards = []
    for finding in findings[:2]:
        fid = finding["id"]
        item = items.get(fid)
        comparisons = []
        if item and item["status"] == "executed":
            program = item["program"]
            states = {s["key"]: s for s in program["states"]}
            for key, first, second in first_step_comparisons(item["result"]["scenario"]):
                field = states[key]
                amounts = " или ".join(quantity(value, field["unit"]) for value in (first, second))
                comparisons.append(f'<h3>{safe(field["label"])}</h3><strong>{amounts}</strong>')
            refs = [program[side]["source"] for side in ("first", "second")]
            links = [f'Правило {letter} · {line_link(ref["line"])}' for letter, ref in zip(("А", "Б"), refs)]
            links.append(link("execution-" + fid, "Расчёт и контроль"))
        elif item and item.get("acceptance_example"):
            result = item["acceptance_example"]["result"]
            computed, expected = (quantity(result[k], result["unit"]) for k in ("computed", "expected"))
            comparisons.append(f'<h3>{safe(result["metric"])}</h3>'
                               f'<strong>{computed} по правилам · {expected} в приёмке</strong>')
            links = [link("execution-" + fid, "Сверка на фиксированном входе"),
                     line_link(result["expected_source"]["line"])]
        else:
            comparisons.append(f'<h3>{safe(finding["title"])}</h3>')
            links = [line_link(n) for n in dict.fromkeys(ref["line"] for ref in finding["evidence"])]
        cards.append(f'<article><small>{link(fid, fid)} · {safe(finding["priority"])} · {safe(finding["owner"])}</small>'
                     f'{"".join(comparisons)}'
                     f'<p>{safe(finding["question"])}</p><p><b>Следующий шаг.</b> {safe(finding["action"])}</p>'
                     f'<p class="brief-refs">Основания · {" · ".join(links)}</p></article>')
    return ('<section class="decision-brief" aria-labelledby="brief-title">'
            '<h2 id="brief-title">Что согласовать в первую очередь</h2>' + "".join(cards) + '</section>')


def reviewed_field_cells(section):
    if not section:
        return {}
    decisions = {a["candidate"]: a for a in section["assessments"]}
    labels = {"required": "Обязательно", "excluded": "Исключено", "optional": "Необязательно"}
    cells = {}
    for c in section["candidates"]:
        row = decisions[c["id"]]
        statuses = {e["example"]: e["http_status"] for e in c["errors"]}
        parts = []
        for ruling in row["rulings"]:
            if ruling["policy"] == "unspecified":
                continue
            codes = ", ".join(str(s) for s in dict.fromkeys(statuses[n] for n in ruling["examples"]))
            refs = ", ".join(line_link(e["line"]) for e in ruling["evidence"])
            parts.append(f'<p>{labels[ruling["policy"]]} для HTTP {codes}. {refs}</p>')
        parts.extend(f'<p>{safe(q)}</p>' for q in row["questions"])
        cells[c["operation"], c["field"]] = "".join(parts)
    return cells


def response_field_comparison(lines, shapes, segments, section=None):
    """Compare complete, observed key sets only within sibling HTTP response sections."""
    indexed = response_fields.response_groups(lines, shapes, segments)
    decisions = reviewed_field_cells(section)
    tables = []
    for parent, examples in indexed.items():
        group = [(ex, (f"{ex['status']} (раздел {ex['heading_status']})"
                       if ex["heading_status"] and ex["heading_status"] != ex["status"] else str(ex["status"])))
                 for ex in examples]
        if len(group) < 2 and not section:
            continue
        keys = list(dict.fromkeys(k for shape, _ in group for k in shape["keys"]))
        rows = []
        for key in keys[:32]:
            present, absent, absent_codes = [], [], []
            for shape, label in group:
                anchor = link(f"source-line-{shape['start']}", f"{label} · строка {shape['start']}")
                if key in shape["keys"]:
                    present.append(anchor)
                elif shape["key_count"] == len(shape["keys"]):
                    absent.append(anchor)
                    absent_codes.append(label)
            decision = decisions.get((parent, key))
            if decision is None and not section and present and absent:
                codes = ", ".join(dict.fromkeys(absent_codes))
                decision = (f'Обязано ли поле <code>{safe(key)}</code> присутствовать в ответах '
                            f'{safe(codes)} для показанных случаев?')
            if decision is not None:
                rows.append(f'<tr><td><code>{safe(key)}</code></td><td>{", ".join(present)}</td>'
                            f'<td>{", ".join(absent) or "—"}</td><td>{decision}</td></tr>')
        if rows:
            limit = '<p>Сопоставлены первые 32 имени поля.</p>' if len(keys) > 32 else ''
            tables.append(f'<p>Раздел ответов · {line_link(parent)}</p><div class="scroll"><table><thead><tr><th>Поле</th>'
                          '<th>Есть в примере</th><th>Отсутствует в примере</th><th>Условия контракта</th>'
                          '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' + limit)
    if not tables:
        return ''
    return ('<section id="response-fields"><h2>Состав ответов по примерам</h2>'
            '<p>Сопоставлены ключи объектов из одного раздела. Проверьте условия по нормативной схеме. '
            'Если она оставляет выбор, передайте вопрос владельцу контракта.</p>'
            + ''.join(tables) + '</section>')


def render_json_checks(checks, lines, shapes=(), segments=(), field_review=None):
    examples = checks.get("examples", [])
    if not examples:
        return ""
    rows = []
    by_start = {entry["start"]: entry for entry in shapes}
    for item in examples:
        status = {"valid": "Корректный JSON", "invalid": "Ошибка синтаксиса",
                  "unchecked": "Проверка не завершена"}[item["status"]]
        detail = ""
        shape = by_start.get(item["start"])
        if shape:
            keys = ", ".join(f"<code>{safe(k)}</code>" for k in shape["keys"])
            extra = shape["key_count"] - len(shape["keys"])
            detail = " · " + (keys or "Пустой объект") + (f" · ещё {extra}" if extra else "")
        if item["status"] != "valid":
            n = item["line"]
            position = line_link(n) if n in lines else f'позиция парсера, строка {n}'
            detail = f' · {safe(item["diagnostic"])} · {position}'
        rows.append(f'<li>{line_link(item["start"])} · <strong>{status}</strong>{detail}</li>')
    return ('<section id="json-syntax"><h2>Синтаксис JSON-примеров</h2>'
            '<p>Парсер проверяет распознанные примеры. Условия обязательности полей и поведение API оцениваются в реестре требований.</p>'
            '<ul>' + "".join(rows) + '</ul></section>' + response_field_comparison(lines, shapes, segments, field_review))


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
    meta_model = safe(run['model'])
    separate = report.get("execution_run")
    if isinstance(separate, dict) and separate.get("model"):
        meta_model += ' · отдельный расчёт: ' + safe(separate['model'])
    title = next((s["section"] for s in segments if s["kind"] == "заголовок"), run["input_name"])
    defects = [f for f in whole["findings"] if f["category"] == "нарушение"]
    critical = [f for f in defects if f["priority"] == "критический"]
    questions = len(whole["findings"]) - len(defects)
    roles = Counter(r["role"] for r in registry)
    forms = Counter(s["kind"] for s in segments)
    backlinks = defaultdict(list)
    lifecycle = report.get("lifecycle_checks", {})
    qa_checks = lifecycle.get("checks", [])
    qa_cases = render_lifecycle(qa_checks, backlinks, lifecycle.get("skipped", ()))
    qa_shortcut = ""
    if qa_cases:
        first = next((i for i, check in enumerate(qa_checks, 1) if check["axis"] == "initial_action"), None)
        caption = f"Сценарии для QA · {len(qa_checks)}" if qa_checks else "Применимость сценариев для QA"
        qa_shortcut = link("qa-scenarios", caption)
        if first:
            qa_shortcut += " · " + link(f"qa-scenario-{first}", "Первое действие")
        qa_shortcut = f"<p>{qa_shortcut}</p>"
    related = defaultdict(list)
    finding_criteria = defaultdict(list)
    decisions = {}
    findings = []
    executions = render_executions(report, lines)
    execution_items = {item["finding_id"]: item for item in report.get("executables", {}).get("items", [])}
    jira_link = ""
    if whole["findings"]:
        payload = b64encode(issues_csv(report)).decode("ascii")
        jira_link = (f'<a class="jira-export" download="review-issues.csv" '
                     f'href="data:text/csv;charset=utf-8;base64,{payload}">Скачать задачи для Jira (CSV)</a>')
    page_by_line = {n: page["page"] for page in run.get("pages", [])
                    for n in range(page["start"], page["end"] + 1)}
    for f in whole["findings"]:
        fid = f["id"]
        for rid in f["requirements"]:
            related[rid].append(link(fid, fid))
            finding_criteria[rid].append(str(f["criterion"]))
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
{executions.get(fid, '')}
{evidence(f['evidence'])}<p><b>{safe(f['owner'])}.</b> {safe(f['question'])}</p>
<p><b>Исправление.</b> {safe(f['action'])}</p><p>{refs}</p>
{finding_download(f, run, page_by_line, execution_items.get(fid))}
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
    registry_sections = defaultdict(list)
    for item in registry:
        rid = item["id"]
        cite = item["evidence"]
        backlinks[cite["line"]].append((rid, rid))
        section_name = blocks_by_start[item["start"]]["section"].removeprefix(title + " / ") or "Без заголовка"
        registry_sections[section_name].append(rid)
        section = safe(section_name)
        quality = "".join(f'<p><b>{safe(check["criterion"].capitalize())}.</b> {safe(check["conclusion"])} '
                          f'{line_link(check["evidence"]["line"])}</p>' for check in item.get("quality_checks", []))
        if not quality and finding_criteria[rid]:
            quality = '<p>Критерии связанных замечаний · ' + safe(", ".join(dict.fromkeys(finding_criteria[rid]))) + '</p>'
        rows.append(f'''<tr id="{rid}"><td><b>{rid}</b><br>{safe(item['original_id'])}</td>
<td><small>{safe(item['role'])} · {section}</small><br><b>{safe(item['statement'])}</b>
<blockquote>{safe(cite['quote'])} {line_link(cite['line'])}</blockquote></td>
<td><small>{safe(item['status'])}</small><br>{safe(item['assessment'])}{quality}<p>{' · '.join(related[rid])}</p></td></tr>''')
    registry_index = "".join(f'<li>{link(ids[0], section)} · {len(ids)}</li>' for section, ids in registry_sections.items())
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
        refs = " · ".join(line_link(n) for n in dict.fromkeys(e["line"] for e in g["evidence"]))
        verdicts.append(f'<article><h3>{label}</h3><strong>{safe(g["status"].capitalize())}</strong>'
                        f'<p>{safe(g["rationale"])}</p><p class="brief-refs">{refs}</p></article>')
    axes = "".join(f'<article class="finding"><h3>{safe(d["criterion"].capitalize())}</h3><p>{safe(d["assessment"])}</p>'
                   f'{evidence(d["evidence"])}</article>' for d in whole["dimensions"])
    sections = [("actions", "Замечания"), ("register", "Реестр"), ("quality", "Оценка набора"),
                ("coverage", "Покрытие"), ("source", "Исходник"), ("passport", "Паспорт")]
    if qa_cases:
        sections.insert(1, ("qa-scenarios", "Сценарии для QA"))
    nav = " · ".join(link(k, v) for k, v in sections)
    summary = (f'{roles["требование"]} требований · {roles["приёмочный пример"]} приёмочных примеров · '
               f'{roles["пример контракта"]} примеров контракта')
    styles = Path(__file__).with_name("render.css").read_text(encoding="utf-8")
    if executions:
        styles += Path(__file__).with_name("execution.css").read_text(encoding="utf-8")
    styles += """
.full nav{margin:1rem 0}.metrics,.gates{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13em),1fr));gap:.8rem;margin:1rem 0}
.metrics>div{padding:.55rem .85rem;border-left:2px solid var(--line)}
.metrics strong{font-size:1.45rem;display:block}.full h3{font-size:1.05rem;margin:.35rem 0}
.gates>article{padding:1rem;background:var(--green);border-top:3px solid var(--deep)}
.gates strong{font-size:1.3rem;color:var(--deep)}.gates p{margin:.4rem 0}
.full .brief-refs{font-size:.86em;color:var(--muted)}
.full section{margin:1.6rem 0}.finding{border-top:1px solid var(--line);padding:1rem 0}
.decision-brief{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr));gap:.8rem}
.decision-brief>h2{grid-column:1/-1;margin:0}.decision-brief article{min-width:0;border-left:3px solid #bd582d;background:#fff5e8;padding:.8rem 1rem}
.decision-brief article>strong{font-size:1.4rem;color:#48271e}.decision-brief article p{margin:.45rem 0}
.full .report-meta{color:var(--muted)}.full .report-toolbar{display:flex;flex-wrap:wrap;align-items:start;justify-content:space-between;gap:.5rem 1rem}
.full .report-toolbar nav{margin:0}.full .report-toolbar summary{font-weight:600;color:var(--deep);padding:.4rem 0}
.full .report-toolbar button{font:inherit;font-weight:600;color:var(--deep);background:white;border:1px solid var(--deep);border-radius:.4rem;cursor:pointer;padding:.4rem .8rem}
.finding p+p{margin-top:.45rem}.full small{color:var(--muted)}.full blockquote{margin:.45rem 0}
.finding-export{display:inline-block;margin:.45rem 0;padding:.25rem .5rem;border:1px solid var(--line);border-radius:3px;font-size:.9rem}
.finding-export:hover{background:var(--green)}
.full table{width:100%;border-collapse:collapse}.full td,.full th{padding:.55rem;vertical-align:top;border-bottom:1px solid var(--line);text-align:left}
.full .print-note{display:none}
.full th{background:var(--green)}.full summary{cursor:pointer}.full tr:target,.finding:target{background:var(--warm)}
.full .scroll{overflow:auto}.full td:first-child{min-width:4.6em}.full section>h2{border-top:2px solid var(--deep);padding-top:1rem}
#response-fields table{table-layout:fixed}#response-fields th:first-child{width:16%}
@media screen and (max-width:680px){#response-fields table{min-width:38rem}}
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
 header,nav,.skip-link,.backlinks,.decision-draft,.finding-export,.full .report-toolbar,.registry-nav,
 .full #coverage details,.full .attempts{display:none}
 .full .print-note{display:block}
 .full #actions.after-brief{break-before:page}
 main.full{width:auto;margin:0;padding:0;border:0;font-size:10pt;line-height:1.35}
 .full h1{font:600 23pt Arial,sans-serif}.full h2{font-size:15pt}.full h3{font-size:11pt}
 .full h2,.full h3{break-after:avoid}.full section{margin:9pt 0}.full section>h2{padding-top:7pt}
 .qa-scenario{break-inside:avoid}
 .decision-brief{grid-template-columns:repeat(2,minmax(0,1fr));gap:6pt}.decision-brief article{padding:5pt 7pt}
 .gates>article{padding:5pt 7pt}.metrics>div{padding:2pt 6pt}.gates p,.decision-brief article p{margin:3pt 0}
 .full .brief-refs{font-size:8pt}
 .metrics{grid-template-columns:repeat(3,1fr)}.gates{grid-template-columns:repeat(2,1fr)}
 .full .registry{table-layout:fixed}.full .registry th:first-child{width:10%}.full .registry th:nth-child(2){width:49%}
 .full .registry thead{display:table-header-group}.full .registry tr{display:table-row}.full .registry td{display:table-cell}
 .full td,.full th{padding:4pt}.full .registry td:first-child{white-space:nowrap}
 .full .registry td p{margin:2pt 0}.full .registry td p:empty{display:none}
 .full .source-row,.full tr,.full blockquote{break-inside:avoid}.full .source-text{font:8.5pt/1.3 Arial,sans-serif}
 .full .finding{padding:6pt 0}.full small{font-size:8pt}.full blockquote{margin:2pt 0}
 .full .facts{display:flex;flex-wrap:wrap;gap:3pt 9pt}.full .facts div{padding:2pt 0}.full .facts dd{margin:1pt 0}
 .full .attempts li{break-inside:avoid}.full .attempts .facts div{border:0}
}
"""
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{PRINT_HASH}'; base-uri 'none'; form-action 'none'">
<title>{safe(title)} · Ревью требований</title><style>{styles}</style></head>
<body><a class="skip-link" href="#report">К результатам</a><header class="masthead"><div class="masthead-inner"><strong>Разбор ТЗ</strong><span>Отчёт полного прогона</span></div></header>
<main class="full" id="report"><small class="report-meta">{safe(run['input_name'])} · {safe(run['started_utc'][:10])} UTC · {meta_model} · версия {safe(run['agent_version'])} · {link('passport', 'Паспорт прогона')}</small>
<h1>{safe(title)}</h1>
<div class="report-toolbar"><nav aria-label="Разделы отчёта"><details><summary>Разделы отчёта</summary>{nav}</details></nav>
{jira_link}<button id="print-report" type="button" hidden>Печать / сохранить PDF</button></div>
<section aria-labelledby="readiness-title"><h2 id="readiness-title">Сводка · можно ли начинать работу</h2><div class="gates">{''.join(verdicts)}</div></section>
<div class="metrics"><div><strong>{len(registry)}</strong>пунктов в реестре</div><div><strong>{len(defects)}</strong>нарушений, включая {len(critical)} критических</div><div><strong>{questions}</strong>открытых вопросов</div></div>
<p>{summary}. Всего замечаний в этом прогоне — {len(whole["findings"])}.</p>{decision_brief(report)}{qa_shortcut}
<section id="actions" class="{'after-brief' if len(findings) >= 2 else ''}"><h2>Все замечания · {len(whole["findings"])}</h2>
{''.join(findings) or '<p>Замечаний не выявлено.</p>'}</section>
<section id="quality"><h2>Качество набора требований</h2>{axes}</section>
{render_json_checks(report.get("json_checks", {}), lines, report.get("example_shapes", ()), report["segments"], report.get("response_field_review"))}
{qa_cases}
<section id="register"><h2>Реестр и проверка каждого пункта</h2><p>Единица учёта — отдельно проверяемое обязательство или пример. Один исходный номер может соответствовать нескольким пунктам. Повторные утверждения из разных разделов остаются прослеживаемыми. Локальная оценка сопровождается ссылками на итоговые замечания или пояснения.</p>
<details class="registry-nav"><summary>К пунктам по разделам документа · {len(registry_sections)}</summary><ul>{registry_index}</ul></details>
<table class="registry"><thead><tr><th scope="col">ID отчёта<br>ID в ТЗ</th><th>Формулировка и цитата</th><th>Проверка пункта</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><h2>Проверка альтернативных прочтений</h2><p>Сценариев-кандидатов: {len(scenario_parts)}. У каждого указано решение итогового ревью.</p>
{''.join(scenario_parts)}{('<h3>Согласованные прочтения</h3><ol>'+''.join(closed)+'</ol>') if closed else ''}</section>
<section id="coverage"><h2>Карта покрытия исходника</h2><p>Учтено {len(lines)} непустых строк, {len(segments)} блоков, {forms['шапка таблицы']} таблиц, {forms['строка таблицы']} строк данных таблиц и {forms['пример json']} блоков JSON. Структура, шаблоны и контекст имеют отдельные записи.</p>
<p class="print-note">Подробная карта блоков и журнал обращений к модели доступны в полном HTML и исходном JSON этого прогона.</p>
<details><summary>Открыть все блоки и основания классификации</summary><table><thead><tr><th>Источник</th><th>Тип</th><th>Назначение и пункты</th></tr></thead><tbody>{''.join(routing)}</tbody></table></details></section>
<section id="source"><h2>Текст исходного документа</h2><ol class="source-list">{render_source(lines,backlinks,run.get("pages", []),run.get("locations", []))}</ol></section>{render_passport(run,2)}</main><script>{PRINT_SCRIPT}</script></body></html>
'''
