"""Render a validated two-rule execution in the workbench and offline report."""

from .render import safe


def render_executions(report, lines):
    from .witness_vm import explain_step

    panels = {}
    for item in report.get("executables", {}).get("items", []):
        why = explain_step(item["program"], lines) if item["status"] == "executed" else None
        panels[item["finding_id"]] = render_execution(item, why)
    return panels


def quantity(value, unit):
    if isinstance(value, str):
        return safe(value)
    digits = f"{value:,}".replace(",", "\u202f").replace("-", "−")
    return digits if unit == "1" else f"{digits} {safe(unit)}"


def values_list(values, definitions, *, keys=False):
    rows = []
    for key, number in values.items():
        field = definitions[key]
        identifier = f' (<code>{safe(key)}</code>)' if keys else ''
        rows.append(f'<div><dt>{safe(field["label"])}{identifier}</dt>'
                    f'<dd>{quantity(number, field["unit"])}</dd></div>')
    return "".join(rows)


def results_table(fields, states, first, second, caption):
    value_type = "enum" if any(states[k]["unit"].startswith("enum:") for k in fields) else "number"
    rows = "".join(
        f'<tr><th scope="row">{safe(states[key]["label"])}</th>'
        f'<td data-rule="Правило А">{quantity(first[key], states[key]["unit"])}</td>'
        f'<td data-rule="Правило Б">{quantity(second[key], states[key]["unit"])}</td></tr>'
        for key in fields)
    return (f'<table class="execution-table" data-values="{value_type}"><caption>{caption}</caption><thead><tr>'
            '<th scope="col">Показатель</th><th scope="col">Правило А</th>'
            f'<th scope="col">Правило Б</th></tr></thead><tbody>{rows}</tbody></table>')


def trace(program, name, outcome, states, inputs):
    first = outcome["first_difference"]
    start = {row["key"]: row["value"] for row in program[name]["initial"]}
    initial = (f'<p class="execution-subtitle">Перед первым событием</p>'
               f'<dl class="execution-values">{values_list(start, states)}</dl>')
    rows = []
    for step in outcome["steps"]:
        number = step["step"]
        differs = first and first["step"] == number
        event_values = values_list(step["inputs"], inputs)
        caption = ("Итог после последнего шага" if number == len(outcome["steps"])
                   else f"Состояние после шага {number}")
        rows.append(
            f'<div class="execution-step{" differs" if differs else ""}">'
            f'<p class="execution-step-title"><span>Шаг {number}</span> {safe(step["label"])}</p>'
            + ('<p class="execution-difference-note">Здесь значения впервые расходятся</p>' if differs else "")
            + (f'<dl class="execution-values">{event_values}</dl>' if event_values else "")
            + results_table(program["observe"], states, step["first"], step["second"], caption) + "</div>")
    return initial + "".join(rows)


def render_explanation(explanation, program, definitions, *, diverged=True):
    if not explanation:
        return ""
    bodies = []
    for name, letter in (("first", "А"), ("second", "Б")):
        line = program[name]["source"]["line"]
        rows = []
        for rule in explanation[name]:
            title = "Условие выполнено" if rule["matched"] else "Условие не выполнено"
            rows.append(f'<p><b>{title}</b> · <code>{safe(rule["when"])}</code></p>')
            if rule["reads"]:
                rows.append(f'<dl class="execution-values">{values_list(rule["reads"], definitions, keys=True)}</dl>')
            for update in rule["updates"]:
                unit = definitions[update["target"]]["unit"]
                if update["applied"]:
                    rows.append(f'<p><code>{safe(update["target"])} = {safe(update["expr"])}</code></p>')
                    if update["reads"]:
                        rows.append(f'<dl class="execution-values">{values_list(update["reads"], definitions, keys=True)}</dl>')
                    rows.append(f'<p>Было {quantity(update["before"], unit)} · стало {quantity(update["after"], unit)}.</p>')
                else:
                    rows.append(f'<p>Обновление пропущено. Осталось {quantity(update["before"], unit)}.</p>')
        bodies.append(f'<div><h5>Правило {letter} · <a href="#source-line-{line}">строка {line}</a></h5>'
                      + ("".join(rows) if rows else '<p>На этом событии правило сохраняет прежнее значение.</p>') + '</div>')
    title = (f'Почему разошлись на шаге {explanation["step"]}' if diverged
             else f'Как рассчитан шаг {explanation["step"]} · {safe(definitions[explanation["field"]]["label"])}')
    return (f'<details class="execution-logic"><summary>{title}</summary>'
            + "".join(bodies) + '</details>')


def explain_simulation(report, finding_id, case, trace):
    """Render the evaluated rules for a custom trace of an already validated report."""
    from .contract import source_lines
    from .witness_vm import explain_step

    item = next(row for row in report["executables"]["items"] if row["finding_id"] == finding_id)
    program = item["program"]
    diff = trace["first_difference"]
    # For agreeing inputs, inspect exactly the field and moment of the saved disagreement.
    point = diff or item["result"]["scenario"]["first_difference"]
    why = explain_step(program, source_lines(report), case=case, step=point["step"], field=point["field"])
    fields = {row["key"]: row for row in program["states"] + program["inputs"]}
    return render_explanation(why, program, fields, diverged=diff is not None)


def render_execution(item, explanation=None):
    fid = safe(item["finding_id"])
    if item["status"] == "unsupported":
        return (f'<div id="execution-{fid}" class="execution execution-unavailable">'
                f'<h4>Расчёт не подтверждён</h4><p>{safe(item["reason"])}</p></div>')
    program, result = item["program"], item["result"]
    states = {s["key"]: s for s in program["states"]}
    inputs = {s["key"]: s for s in program["inputs"]}
    example = result["scenario"]
    difference = example["first_difference"]
    definition = states[difference["field"]]
    step = example["steps"][difference["step"] - 1]
    symbolic = definition["unit"].startswith("enum:")
    comparisons = []
    for side, letter in (("first", "А"), ("second", "Б")):
        source = program[side]["source"]
        line = source["line"]
        value = quantity(difference[side], definition["unit"])
        if symbolic:
            value = value.replace("_", "_<wbr/>")
        comparisons.append(
            f'<div class="execution-side"><dt>Правило {letter} · '
            f'<a href="#source-line-{line}">строка {line}</a></dt>'
            f'<dd>{value}</dd></div>')
    rules = ""
    for key, letter in (("first", "А"), ("second", "Б")):
        source = program[key]["source"]
        line = source["line"]
        rules += (f'<blockquote><span>Правило {letter} · <a href="#source-line-{line}">строка {line}</a></span>'
                  f'{safe(source["quote"])}</blockquote>')
        reading = item.get("audit", {}).get(key + "_rule")
        if reading:
            rules += f'<p class="execution-reading">{safe(reading)}</p>'
    full_trace = trace(program, "scenario", example, states, inputs)
    control = trace(program, "control", result["control"], states, inputs)
    event_inputs = values_list(step["inputs"], inputs)
    final = (results_table(program["observe"], states, example["final"]["first"], example["final"]["second"],
                           "Итог после последнего шага")
             if step["step"] < len(example["steps"]) else "")
    return (f'<div class="execution" id="execution-{fid}">'
            '<p class="execution-caption">Исполняемый пример</p>'
            f'<h4>{safe(definition["label"])}</h4>'
            f'<dl class="execution-comparison" data-values="{"enum" if symbolic else "number"}">{"".join(comparisons)}</dl>'
            f'<p class="execution-break"><b>Первое расхождение · шаг {step["step"]}</b>'
            f'<span>{safe(step["label"])}</span></p>'
            + (f'<dl class="execution-values execution-shared" aria-label="Общий вход этого шага">{event_inputs}</dl>'
               if event_inputs else "")
            + final
            + render_explanation(explanation, program, {**states, **inputs})
            + f'<details class="execution-trace"><summary>Входные данные и шаги расчёта</summary>{full_trace}</details>'
            f'<details class="execution-sources"><summary>Две цитаты из ТЗ</summary>{rules}</details>'
            f'<details class="execution-control"><summary>Контрольный пример · исходы совпали</summary>{control}</details>'
            '</div>')
