"""Check the syntax of explicitly labelled JSON examples against the saved source."""

import json
import re

from .segments import FENCE


TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|(?P<constant>NaN|-?Infinity)')


def canonical_diagnostic(item, lines):
    # Python 3.14 reports a trailing comma at the comma. Earlier decoders report
    # it at the closing delimiter. Keep one source location in portable reports.
    trailing = {"Illegal trailing comma before end of object": ("}", "Expecting property name enclosed in double quotes"),
                "Illegal trailing comma before end of array": ("]", "Expecting value")}
    if not isinstance(item, dict) or not isinstance(item.get("diagnostic"), str):
        return item
    rule = trailing.get(item["diagnostic"])
    if not rule:
        return item
    line, column, end = (item.get(key) for key in ("line", "column", "end"))
    if not all(type(x) is int for x in (line, column, end)) or not 1 <= line <= end <= len(lines):
        return item
    if not 1 <= column <= len(lines[line - 1]) or lines[line - 1][column - 1] != ",":
        return item
    closing, message = rule
    for n in range(line, end + 1):
        start = column if n == line else 0
        for pos, char in enumerate(lines[n - 1][start:], start + 1):
            if char not in " \t\r\n":
                if char == closing:
                    return dict(item, line=n, column=pos, diagnostic=message)
                return item
    return item


def example_body(lines, start, end):
    """Return the first body line (one-based) and text of a fenced or printed JSON example."""
    fence = FENCE.match(lines[start - 1].strip())
    first, finish = start - 1, end
    if fence:
        marker, last = fence[1], lines[end - 1].strip()
        first += 1
        finish -= int(end > start and len(last) >= len(marker) and set(last) == {marker[0]})
    return first + 1, "\n".join(line if line.strip() else "" for line in lines[first:finish])


def inspect_examples(lines, segments):
    results = []
    for block in segments:
        if block["kind"] != "пример json":
            continue
        start, end = block["start"], block["end"]
        first, text = example_body(lines, start, end)
        item = {"start": start, "end": end, "status": "valid"}
        try:
            def reject_constant(value):
                # The Python decoder accepts these extensions by default.
                pos = next(m.start() for m in TOKENS.finditer(text) if m["constant"] == value)
                raise json.JSONDecodeError("Недопустимое числовое значение", text, pos)

            json.loads(text, parse_int=str, parse_float=str, parse_constant=reject_constant)
        except json.JSONDecodeError as exc:
            line = min(first + exc.lineno - 1, end)
            item.update(status="invalid", line=line, column=exc.colno, diagnostic=exc.msg)
        except RecursionError:
            item.update(status="unchecked", line=start, diagnostic="Превышена глубина вложенности парсера")
        results.append(canonical_diagnostic(item, lines))
    return {"version": 1, "examples": results}


def verify_saved_checks(saved, lines, segments):
    expected = inspect_examples(lines, segments)
    if isinstance(saved, dict) and isinstance(saved.get("examples"), list):
        saved = dict(saved, examples=[canonical_diagnostic(item, lines) for item in saved["examples"]])
    if saved != expected:
        raise ValueError("Результат проверки JSON расходится с исходником")
    return expected


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
