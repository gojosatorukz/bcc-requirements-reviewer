"""Render the actual owner conversation as an offline handoff to an analyst or QA."""

import re

from .clarify import validate_transcript
from .render import safe


def render_dialogue(record):
    fid, digest = record["finding_id"], record["baseline_sha256"]
    if not re.fullmatch(r"D-[0-9]+", fid) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("В записи нет идентификатора замечания и хеша исходного отчёта")
    validate_transcript(record, fid, digest)
    ready = record["state"] == "ready"
    turns = []
    for number, turn in enumerate(record["turns"], 1):
        turns.append(f'<article><p class="origin">Ответ {number}</p>'
                     f'<h2>{safe(turn["question"])}</h2><blockquote>{safe(turn["answer"])}</blockquote>'
                     f'<p>{safe("Требуется уточнение" if turn["state"] == "needs_followup" else "Правило готово для проверки")}</p></article>')
    known = "".join("<li>" + safe(row["text"]) + "</li>" for row in record["commitments"])
    follow = "" if ready else '<h2>Следующий вопрос</h2><p>' + safe(record["follow_up"]) + '</p>'
    rule = '<h2>Согласованные условия</h2><ul>' + known + '</ul>' if known else ""
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Диалог с владельцем · {safe(fid)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#fffaef;color:#231d26;font:1.05rem/1.55 system-ui,sans-serif}}
main{{max-width:49rem;margin:auto;padding:1.5rem clamp(1rem,5vw,3rem) 3rem}}header{{background:#ff7045;color:#231d26;padding:.9rem max(1rem,calc((100% - 43rem)/2));font-weight:800}}
h1{{font-size:clamp(2rem,6vw,3.25rem);line-height:1.08;margin:.6rem 0}}h2{{font-size:1.25rem;line-height:1.35}}
article{{border-top:1px solid #bbae9b;padding:1rem 0}}blockquote{{margin:.6rem 0;padding:.7rem 1rem;background:#fff;border-left:4px solid #cb5c2a}}
.origin,footer{{color:#544c4b;font-size:.88rem}}footer{{border-top:1px solid #bbae9b;padding-top:1rem;overflow-wrap:anywhere}}
@media print{{article{{break-inside:avoid}}body{{background:white}}}}</style>
<header>Разбор ТЗ</header><main><p class="origin">Сохранённый диалог · {safe(fid)}</p>
<h1>Ответ владельца</h1><p><b>Замечание открыто.</b> {"Согласованное правило готово для проверки по следующей редакции." if ready else "Агенту нужно уточнить условия."}</p>
{"".join(turns)}{rule}{follow}<footer>SHA-256 базового отчёта<br><code>{safe(digest)}</code></footer></main></html>'''
