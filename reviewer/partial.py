"""Source-backed checks that can finish when a full Gemini review hits its quota."""

from html import escape
import re

from .json_examples import inspect_examples
from .partial_boundary import boundary_questions
from .partial_contract import contract_questions
from .segments import segment
from .structure import describe_error


OUTCOME = re.compile(r"решение|результат|ожидаемый результат|статус|действие|маршрут|ответ|outcome|expected result|decision|status|action|result", re.I)
MISSING = r"(?:TBD|TBC|TODO|уточняется|будет определ[её]н[ао]? позднее)"
LABEL_GAP = re.compile(rf"^\s*(?:[-*]\s+)?([^:\n]{{1,180}}?)\s*:\s*({MISSING})[.!]?\s*$", re.I)
VAGUE_TIME = re.compile(r"\b(?:быстро|оперативно|своевременно|незамедлительно|quickly|promptly)\b", re.I)
OBLIGATION = re.compile(r"\b(?:долж(?:ен|на|но|ны)|обязан[аы]?|shall|must)\b", re.I)
TIME_BOUND = re.compile(r"\d\s*(?:мс|с\b|сек|мин|ч\b|час|ms\b|sec|min|hour)", re.I)
MAX_FINDINGS = 60
SCOPE = ("Проверены таблицы решений и явно заданные числовые границы с соседними примерами, "
         "незаполненные параметры, требования о скорости, синтаксис JSON и явное число обязательных полей в примерах успешного ответа. "
         "Полная оценка смысла требований требует запуска модели.")


def cells(text):
    if "\\|" in text or any("|" in span for span in re.findall(r"`[^`]*`", text)):
        # Simple tables only. A pipe inside a code span or an escaped pipe needs a Markdown parser.
        return []
    return [c.strip() for c in text.strip().strip("|").split("|")]


def brief(text):
    return text if len(text) <= 160 else text[:157] + "…"


def build_partial(name, lines, digest, metadata, model):
    blocks = segment(lines, metadata.get("pdf_json_blocks", ()))
    results = []
    total = 0

    def record(rule, title, question, evidence):
        nonlocal total
        total += 1
        if len(results) < MAX_FINDINGS:
            results.append({"rule": rule, "title": title, "question": question,
                            "evidence": [{"line": n, "quote": lines[n-1]} for n in evidence]})

    timed_sections = {b["section"] for b in blocks if b["kind"] in ("текст", "строка таблицы") and
                      TIME_BOUND.search(lines[b["start"]-1])}
    grouped = {}
    for block in blocks:
        n, kind = block["start"], block["kind"]
        if kind == "текст" and (missing := LABEL_GAP.fullmatch(lines[n-1])):
            label = missing[1].strip()
            record("missing_parameter", f"Не задано значение «{label}»",
                   f"Какое значение следует установить для параметра «{label}»?", [n])
        if (kind == "текст" and block["section"] not in timed_sections and OBLIGATION.search(lines[n-1])
                and (timing := VAGUE_TIME.search(lines[n-1]))):
            record("unmeasured_time", f"Уточните время для условия «{timing[0]}»",
                   f"Какой предельный срок и какие начало и конец замера соответствуют «{timing[0]}» в строке {n}?", [n])
        if kind != "строка таблицы":
            continue
        header = block["header"]
        if header not in grouped:
            grouped[header] = (cells(lines[header-1]), {})
        columns, conditions = grouped[header]
        values = cells(lines[n-1])
        if len(values) != len(columns) or len(values) < 2:
            continue
        for column, value in zip(columns, values):
            if re.fullmatch(MISSING, value, re.I):
                record("missing_parameter", f"Не заполнен столбец «{brief(column)}»",
                       f"Какое значение должно стоять в столбце «{brief(column)}» строки {n}?", [n])
        if not OUTCOME.fullmatch(columns[-1]) or not all(values) or any(re.fullmatch(MISSING, v, re.I) for v in values):
            continue
        key, output = tuple(values[:-1]), values[-1]
        observed = conditions.setdefault(key, {})
        if output in observed:
            continue
        if observed:
            previous, first_line = next(iter(observed.items()))
            context = brief(", ".join(brief(col) + " = " + brief(val) for col, val in zip(columns, key)))
            record("table_outcomes", "Одни условия ведут к разным результатам",
                   f"При условиях «{context}» что ожидается: «{brief(previous)}», «{brief(output)}» или оба результата?", [first_line, n])
        observed[output] = n

    for question in boundary_questions(lines, blocks, cells):
        record(*question)

    checks = inspect_examples(lines, blocks)
    for question in contract_questions(lines, blocks, checks):
        record(*question)
    for item in checks["examples"]:
        if item["status"] == "valid":
            continue
        n = item["line"]
        if item["status"] == "invalid":
            detail = describe_error(item, lines)
            record("invalid_json", f"Ошибка JSON в строке {n}. {detail}",
                   f"Как должен выглядеть корректный JSON-пример на месте строки {n}?", [n])
        else:
            record("unchecked_json", "Не удалось разобрать глубоко вложенный JSON",
                   f"Какой минимальный пример должен воспроизводить контракт из строки {n}?", [n])

    results.sort(key=lambda value: value["evidence"][0]["line"])
    return {"report_type": "partial", "schema_version": 1, "reason": "gemini_quota", "attempted_model": model,
            "input_name": name, "input_sha256": digest, "scope": SCOPE, "total_findings": total, "findings": results}


def render_partial(report):
    findings = []
    citations = {}
    for finding in report["findings"]:
        links = []
        for ref in finding["evidence"]:
            line = int(ref["line"])
            citations[line] = ref["quote"]
            links.append(f'<a href="#line-{line}">Строка {line}</a>')
        findings.append(f'<article><h2>{escape(finding["title"])}</h2>'
                        f'<p class="question">{escape(finding["question"])}</p><p>{" · ".join(links)}</p></article>')
    empty = "<p>В пределах этих проверок замечаний нет.</p>"
    excerpts = "".join(f'<p id="line-{n}"><b>Строка {n}</b><br><code>{escape(t)}</code></p>' for n, t in sorted(citations.items()))
    return (f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">'
            '<title>Частичный отчёт · Разбор ТЗ</title><style>body{margin:0;background:#fbf7ee;color:#201c20;font:17px/1.55 system-ui}'
            'main{max-width:48rem;margin:auto;padding:1.5rem}header{border-top:7px solid #fa672b}article{background:white;padding:1rem 1.25rem;'
            'border:1px solid #c9c2b6;border-radius:12px;margin:1rem 0}h1,h2{line-height:1.17}h2{font-size:1.18rem}a{color:#773915}'
            'p,h1,h2,code{overflow-wrap:anywhere}:target{background:#ffe1a5}code{white-space:pre-wrap}</style><main><header>'
            f'<h1>Частичный отчёт</h1><p>{escape(report["input_name"])}</p></header><p>Модель {escape(report["attempted_model"])} '
            'достигла лимита. Замечания ниже сформированы локальными правилами.</p>'
            f'<p>{escape(report["scope"])}</p><p>Показано замечаний: {len(findings)} из {int(report["total_findings"])}.</p>'
            + ("".join(findings) or empty) + (f'<section><h2>Строки исходного ТЗ</h2>{excerpts}</section>' if excerpts else "")
            + '</main></html>')
