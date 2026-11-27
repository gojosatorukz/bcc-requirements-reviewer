"""Render a validated two-rule execution in the workbench and offline report."""

from .execution_boundary import MAX_REPLAYS_PER_REPORT, scan_boundary
from .executable import unexecuted_message
from .render import safe


def render_executions(report, lines):
    from .witness_vm import explain_step

    panels = {}
    remaining = MAX_REPLAYS_PER_REPORT
    for item in report.get("executables", {}).get("items", []):
        why = explain_step(item["program"], lines) if item["status"] == "executed" else None
        boundary, used = scan_boundary(item, lines, remaining)
        remaining -= used
        separate = report.get("execution_run")
        label = ""
        if isinstance(separate, dict) and separate.get("model"):
            label = (f'<p class="execution-caption">Исходное ревью: {safe(report["run"]["model"])}. '
                     f'Отдельный расчёт: {safe(separate["model"])}.</p>')
        panels[item["finding_id"]] = render_execution(item, why, boundary, label)
    return panels


def quantity(value, unit):
    if isinstance(value, str):
        return safe(value)
    return safe(numeric_quantity(value, unit))


def numeric_quantity(value, unit):
    digits = format(value, ",").replace(",", "\u202f").replace("-", "−")
    return digits if unit == "1" else f"{digits} {unit}"


def acceptance_trace(example):
    """Ordered explanations with the exact sources of inputs and arithmetic, as plain text."""
    program, result = example["program"], example["result"]
    unit = result["unit"]
    yield "Условия примера", program["scope"]
    yield f"Начальное значение · {numeric_quantity(result['initial'], unit)}", [program["initial"]["source"]]
    for number, (step, instruction) in enumerate(zip(result["steps"], program["steps"]), 1):
        before, after = (numeric_quantity(step[k], unit) for k in ("before", "after"))
        operation = instruction["operation"]
        refs = [step["source"]]
        if operation == "keep":
            calculation = f"{before} → {after} (без изменения)"
        else:
            operand = instruction["operands"][0]["source"]
            if operand not in refs:
                refs.append(operand)
            delta = numeric_quantity(abs(step["after"] - step["before"]), unit)
            sign = "+" if operation == "add" else "−"
            calculation = f"{before} {sign} {delta} = {after}"
        yield f"Шаг {number}. {step['label']} · {calculation}", refs


def acceptance_lines(item):
    """Portable acceptance check for the finding's Jira and Markdown handoffs."""
    example = item["acceptance_example"]
    result = example["result"]
    yield f"Сверка на фиксированном входе · {result['metric']}"
    yield f"Расчёт по правилам ТЗ · {numeric_quantity(result['computed'], result['unit'])}"
    yield f"Ожидание из приёмки · {numeric_quantity(result['expected'], result['unit'])}"
    expected = result["expected_source"]
    yield f"Строка {expected['line']} · {expected['quote']}"
    for label, refs in acceptance_trace(example):
        yield label
        for ref in refs:
            yield f"Строка {ref['line']} · {ref['quote']}"
    yield "Расчёт двух трактовок не подтверждён. " + item["reason"]


def render_acceptance(item):
    example = item["acceptance_example"]
    result = example["result"]
    unit, metric = result["unit"], safe(result["metric"])
    def citation(ref):
        line = ref["line"]
        return (f'<blockquote><span><a href="#source-line-{line}">Строка {line}</a></span>'
                f'{safe(ref["quote"])}</blockquote>')
    rows = "".join(f'<p class="execution-step-title">{safe(label)}</p>' + "".join(map(citation, refs))
                   for label, refs in acceptance_trace(example))
    return (f'<div class="execution" id="execution-{safe(item["finding_id"])}" data-kind="fixed-acceptance">'
            '<p class="execution-caption">Сверка на фиксированном входе</p>'
            f'<h4>{metric}</h4><dl class="execution-comparison" aria-label="{metric}">'
            '<div class="execution-side"><dt>Расчёт по правилам ТЗ</dt>'
            f'<dd>{quantity(result["computed"], unit)}</dd></div>'
            '<div class="execution-side"><dt>Ожидание из приёмки</dt>'
            f'<dd>{quantity(result["expected"], unit)}</dd></div></dl>'
            f'<div class="execution-sources">{citation(result["expected_source"])}</div>'
            f'<details class="execution-sources" open="open"><summary>Как получен результат</summary>{rows}</details>'
            '<details><summary>Почему расчёт двух трактовок не подтверждён</summary>'
            f'<p>{safe(item["reason"])}</p></details></div>')


def first_step_comparisons(trace):
    first = trace["first_difference"]
    step = trace["steps"][first["step"] - 1]
    for key in step.get("different", [first["field"]]):
        yield key, step["first"][key], step["second"][key]


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


def render_boundary(boundary, states, inputs):
    if not boundary:
        return ""
    step = boundary["step"]
    input_field = (inputs if step else states)[boundary["key"]]
    unit = input_field["unit"]
    where = f"на шаге {step}" if step else "до первого шага"
    start, end = (quantity(boundary[k], unit) for k in ("start", "end"))
    intro = (f'<p>{safe(input_field["label"])} {where}. Поиск от контрольного значения {start} '
             f'к значению {end} из примера с шагом в одну единицу. Остальные входы зафиксированы.</p>')
    if boundary["status"] == "limited":
        result = f'<p>Проверено до {quantity(boundary["last"], unit)} включительно. '
        result += 'На этом участке результаты совпали. Поиск остановлен по лимиту вычислений.</p>'
    elif boundary["status"] == "undefined":
        result = (f'<p>При {quantity(boundary["failed"], unit)} программа не смогла вычислить результат. '
                  'Поиск остановлен на этом значении.</p>')
    else:
        field = states[boundary["output"]]
        caption = f'{safe(field["label"])} после шага {boundary["output_step"]}'
        rows = []
        for name, label in (("equal", "Совпадение"), ("different", "Первое расхождение")):
            a, b = (quantity(v, field["unit"]) for v in boundary[name + "_values"])
            value = quantity(boundary[name + "_input"], unit)
            rows.append(f'<tr><th scope="row">{label} · {value}</th>'
                        f'<td data-rule="Правило А">{a}</td><td data-rule="Правило Б">{b}</td></tr>')
        result = (f'<table class="execution-table"><caption>{caption}</caption><thead><tr>'
                  '<th scope="col">Значение входа</th><th scope="col">Правило А</th>'
                  '<th scope="col">Правило Б</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>')
    return '<details class="execution-trace execution-boundary"><summary>Граница расхождения двух программ</summary>' + intro + result + '</details>'


def render_execution(item, explanation=None, boundary=None, model_note=""):
    fid = safe(item["finding_id"])
    if item["status"] != "executed":
        if item.get("acceptance_example"):
            return render_acceptance(item)
        title, reason = unexecuted_message(item)
        return (f'<div id="execution-{fid}" class="execution execution-unavailable">'
                f'<h4>{safe(title)}</h4><p>{safe(reason)}</p></div>')
    program, result = item["program"], item["result"]
    states = {s["key"]: s for s in program["states"]}
    inputs = {s["key"]: s for s in program["inputs"]}
    example = result["scenario"]
    difference = example["first_difference"]
    step = example["steps"][difference["step"] - 1]
    comparisons = []
    for key, first, second in first_step_comparisons(example):
        definition = states[key]
        symbolic = definition["unit"].startswith("enum:")
        sides = []
        for side, letter, observed in (("first", "А", first), ("second", "Б", second)):
            line = program[side]["source"]["line"]
            value = quantity(observed, definition["unit"])
            if symbolic:
                value = value.replace("_", "_<wbr/>")
            sides.append(f'<div class="execution-side"><dt>Правило {letter} · '
                         f'<a href="#source-line-{line}">строка {line}</a></dt><dd>{value}</dd></div>')
        label = safe(definition["label"])
        comparisons.append(f'<h4>{label}</h4><dl class="execution-comparison" aria-label="{label}" '
                           f'data-values="{"enum" if symbolic else "number"}">{"".join(sides)}</dl>')
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
    history = ""
    if "history_proof" in item:
        rule = item["history_proof"]["rule"]
        sample = rule["scenario"]
        line = sample["source"]["line"]
        history = (f'<p>Исходное значение <strong>{safe(sample["initial"])}</strong>, '
                   f'к моменту наблюдения <strong>{safe(sample["current"])}</strong>. '
                   f'<a href="#source-line-{line}">Условия в строке {line}</a>. '
                   'Проверены два сценария из ТЗ.</p>')
    return (f'<div class="execution" id="execution-{fid}">'
            '<p class="execution-caption">Исполняемый пример</p>' + model_note
            + "".join(comparisons) + history
            + ('<p>Для обеих трактовок проверены два измерения из приёмки. '
               'Для других входов потребуется уточнить ожидаемый результат.</p>' if "route_proof" in item else "")
            + f'<p class="execution-break"><b>Первое расхождение · шаг {step["step"]}</b>'
            f'<span>{safe(step["label"])}</span></p>'
            + (f'<dl class="execution-values execution-shared" aria-label="Общий вход этого шага">{event_inputs}</dl>'
               if event_inputs else "")
            + final
            + render_explanation(explanation, program, {**states, **inputs})
            + render_boundary(boundary, states, inputs)
            + f'<details class="execution-trace"><summary>Входные данные и шаги расчёта</summary>{full_trace}</details>'
            f'<details class="execution-sources"><summary>Две цитаты из ТЗ</summary>{rules}</details>'
            f'<details class="execution-control"><summary>Контрольный пример · исходы совпали</summary>{control}</details>'
            '</div>')
