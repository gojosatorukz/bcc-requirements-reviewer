"""Check the syntax of explicitly labelled JSON examples against the saved source."""

import json
import re

from .segments import FENCE


TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|(?P<constant>NaN|-?Infinity)')


def inspect_examples(lines, segments):
    results = []
    for block in segments:
        if block["kind"] != "пример json":
            continue
        start, end = block["start"], block["end"]
        marker = FENCE.match(lines[start - 1].strip())[1]
        last = lines[end - 1].strip()
        closed = end > start and len(last) >= len(marker) and set(last) == {marker[0]}
        body = lines[start:end - int(closed)]
        text = "\n".join(line if line.strip() else "" for line in body)
        item = {"start": start, "end": end, "status": "valid"}
        try:
            def reject_constant(value):
                # The Python decoder accepts these extensions by default.
                pos = next(m.start() for m in TOKENS.finditer(text) if m["constant"] == value)
                raise json.JSONDecodeError("Недопустимое числовое значение", text, pos)

            json.loads(text, parse_int=str, parse_float=str, parse_constant=reject_constant)
        except json.JSONDecodeError as exc:
            line = min(start + exc.lineno, end)
            item.update(status="invalid", line=line, column=exc.colno, diagnostic=exc.msg)
        except RecursionError:
            item.update(status="unchecked", line=start, diagnostic="Превышена глубина вложенности парсера")
        results.append(item)
    return {"version": 1, "examples": results}


def check_findings(checks, findings):
    for item in checks["examples"]:
        if item["status"] != "invalid":
            continue
        for finding in findings:
            description = " ".join(finding[k] for k in ("title", "reason", "action")).casefold()
            if (finding["category"] == "нарушение" and "json" in description
                    and any(item["start"] <= ref["line"] <= item["end"] for ref in finding["evidence"])):
                break
        else:
            raise ValueError(f"Нужно подтвердить ошибку синтаксиса JSON в блоке со строки {item['start']} и процитировать этот блок")
