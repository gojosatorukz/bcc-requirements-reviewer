"""Build a self-contained HTML report from the version 1 review JSON."""

import argparse
from collections import defaultdict
from html import escape
import json
from pathlib import Path
import tempfile

from .contract import REVIEW, source_lines, validate_shape


RUN_FIELDS = {
    "input_name": "Документ",
    "input_lines": "Строк в документе",
    "input_sha256": "SHA-256 документа",
    "provider": "Провайдер",
    "model": "Модель",
    "agent_version": "Версия агента",
    "started_utc": "Начало · UTC",
    "elapsed_seconds": "Длительность · с",
}
TOKEN_LABELS = {
    "input_tokens": "Вход",
    "cached_input_tokens": "Вход из кеша",
    "cache_write_input_tokens": "Запись в кеш",
    "output_tokens": "Выход",
    "reasoning_output_tokens": "Рассуждение",
}
ATTEMPT_STATUS = {"accepted": "Принята", "rejected": "Отклонена"}


def safe(value):
    return escape(str(value))


def render_outcome(outcome, conflict_number, side_number, lines, backlinks):
    source = outcome["source"]
    line = source["line"]
    quote = source["quote"]
    if line not in lines or quote not in lines[line]:
        raise ValueError(f"Конфликт {conflict_number}, исход {side_number}: цитата не найдена в строке {line}")

    evidence_id = f"conflict-{conflict_number}-outcome-{side_number}"
    backlinks[line].append((evidence_id, f"Конфликт {conflict_number} · исход {side_number}"))
    return f"""
      <div class="outcome" id="{evidence_id}" tabindex="-1">
        <h3>Исход {side_number}</h3>
        <p class="outcome-text">{safe(outcome['outcome'])}</p>
        <blockquote>{safe(quote)}</blockquote>
        <a class="citation" href="#source-line-{line}">К строке {line} в ТЗ ↓</a>
      </div>"""


def render_conflict(number, conflict, lines, backlinks):
    outcomes = "".join(
        render_outcome(conflict[side], number, side_number, lines, backlinks)
        for side_number, side in enumerate(("first", "second"), 1)
    )
    return f"""
    <article class="conflict" aria-labelledby="conflict-{number}">
      <h2 id="conflict-{number}">{safe(conflict['title'])}</h2>
      <section class="scenario">
        <h3>Сценарий</h3>
        <p>{safe(conflict['scenario'])}</p>
      </section>
      <div class="outcomes">{outcomes}
      </div>
      <div class="follow-up">
        <section class="question">
          <h3>Вопрос для решения</h3>
          <p class="question-text">{safe(conflict['question'])}</p>
          <p class="owner"><span>Владелец решения</span> {safe(conflict['owner'])}</p>
        </section>
        <section class="control">
          <h3>Где правила совпадают</h3>
          <p>{safe(conflict['agreement_control'])}</p>
        </section>
      </div>
    </article>"""


def render_source(lines, backlinks, pages=()):
    page_by_line = {n: p["page"] for p in pages for n in range(p["start"], p["end"]+1)}
    rows = []
    for line, content in sorted(lines.items()):
        page = f' · с. {int(page_by_line[line])}' if line in page_by_line else ""
        links = "".join(f'<a href="#{target}">↑ {label}</a>' for target, label in backlinks[line])
        references = f'<div class="backlinks">{links}</div>' if links else ""
        rows.append(f"""
        <li class="source-row" id="source-line-{line}" value="{line}" tabindex="-1">
          <span class="line-number"><span class="sr-only">Строка </span>{line}{page}</span>
          <span class="source-text">{safe(content)}</span>{references}
        </li>""")
    return "".join(rows)


def render_passport(run, schema_version):
    fields = [(label, run[key]) for key, label in RUN_FIELDS.items()]
    if "max_workers" in run:
        fields.append(("Лимит параллельных запросов", run["max_workers"]))
    if "engine_sha256" in run:
        fields.append(("SHA-256 проверяющего кода", run["engine_sha256"]))
    fields.append(("Версия формата JSON", schema_version))
    return f"""
    <section class="report-section" aria-labelledby="passport">
      <h2 id="passport" tabindex="-1">Паспорт прогона</h2>
      {render_fields(fields)}
      <h3 class="tokens-heading">Токены</h3>
      {render_usage(run['usage'])}
{render_attempts(run.get('attempts'))}
    </section>"""


def render_usage(usage):
    if usage is not None and type(usage) is not dict:
        raise ValueError("usage: ожидался объект или null")
    if not usage:
        return "<p>Нет данных</p>"
    return render_fields((TOKEN_LABELS.get(key, key), value) for key, value in usage.items())


def render_attempts(attempts):
    if not attempts:
        return ""
    items = []
    for attempt in attempts:
        status = attempt["status"]
        stage = safe(attempt["stage"]) + " · " if "stage" in attempt else ""
        restored = " · из контрольной точки" if attempt.get("restored") else ""
        duration = f" · {safe(attempt['seconds'])} с" if "seconds" in attempt else ""
        reason = f"<p>{safe(attempt['reason'])}</p>" if "reason" in attempt else ""
        items.append(f"""
        <li>
          <p><strong>{stage}{safe(ATTEMPT_STATUS.get(status, status))}{duration}{restored}</strong></p>{reason}
          {render_usage(attempt['usage'])}
        </li>""")
    return f"""
      <details class="attempts">
        <summary>Попытки · {len(attempts)}</summary>
        <ol>{"".join(items)}
        </ol>
      </details>"""


def render_fields(fields):
    items = "".join(f"<div><dt>{safe(label)}</dt><dd>{safe(value)}</dd></div>" for label, value in fields)
    return f'<dl class="facts">{items}</dl>'


def render_report(report):
    version = report.get("schema_version") if isinstance(report, dict) else None
    if type(version) is int and version == 2:
        from .full_render import render_full
        return render_full(report)
    if type(version) is not int or version != 1:
        raise ValueError("Поддерживаются версии 1 и 2 формата отчёта")
    conflicts = report["conflicts"]
    validate_shape({"conflicts": conflicts}, REVIEW, "review")
    lines = source_lines(report)
    backlinks = defaultdict(list)
    if conflicts:
        review = "".join(render_conflict(n, item, lines, backlinks) for n, item in enumerate(conflicts, 1))
    else:
        review = """
    <section class="empty" aria-labelledby="empty-heading">
      <h2 id="empty-heading">Поведенческие противоречия не найдены</h2>
      <p>Модель вернула 0 конфликтов. Этот вывод относится к данному прогону и его области проверки.</p>
    </section>"""

    name = safe(report["run"]["input_name"])
    styles = Path(__file__).with_name("render.css").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>Разбор ТЗ · {name}</title>
  <style>
{styles}
  </style>
</head>
<body>
  <a class="skip-link" href="#report">К отчёту</a>
  <header class="masthead">
    <div class="masthead-inner">
      <strong>Разбор ТЗ</strong>
      <span>Офлайн-отчёт</span>
    </div>
  </header>
  <main id="report" tabindex="-1">
    <div class="intro">
      <p class="scope">{safe(report['scope'])}</p>
      <h1>{name}</h1>
      <div class="overview">
        <p class="count">Конфликты <strong>{len(conflicts)}</strong></p>
        <nav aria-label="Разделы отчёта">
          <a href="#source">Текст ТЗ ↓</a>
          <a href="#passport">Паспорт прогона ↓</a>
        </nav>
      </div>
    </div>
{review}
    <section class="report-section" aria-labelledby="source">
      <h2 id="source" tabindex="-1">Текст ТЗ</h2>
      <p class="section-note">Непустые строки из снимка прогона с исходными номерами.</p>
      <ol class="source-list" role="list">{render_source(lines, backlinks, report['run'].get('pages', []))}
      </ol>
    </section>
{render_passport(report['run'], version)}
  </main>
</body>
</html>
"""


def write_html(path, html):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(html)
            temp.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Построить автономный HTML-отчёт по результату проверки")
    parser.add_argument("input", type=Path, metavar="report.json")
    parser.add_argument("output", type=Path, metavar="report.html|report.pdf")
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve() or (
            args.output.exists() and args.output.samefile(args.input)
        ):
            raise ValueError("Пути JSON и HTML должны различаться")
        report = json.loads(args.input.read_text(encoding="utf-8-sig"))
        html = render_report(report)
        if args.output.suffix.lower() == ".pdf":
            from .pdf_output import write_pdf
            write_pdf(args.output, html)
        elif args.output.suffix.lower() == ".html":
            write_html(args.output, html)
        else:
            raise ValueError("Укажите выходной файл .html или .pdf")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Не удалось создать HTML-отчёт: {exc}\n")
    print(f"Отчёт сохранён: {args.output}")


if __name__ == "__main__":
    main()
