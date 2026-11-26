"""Render a validated two-rule execution in the workbench and offline report."""

from .render import safe


def quantity(value, unit):
    digits = f"{value:,}".replace(",", "\u202f").replace("-", "−")
    return digits if unit == "1" else f"{digits} {safe(unit)}"


def values_list(values, definitions):
    return "".join(f'<div><dt>{safe(definitions[key]["label"])}</dt>'
                   f'<dd>{quantity(number, definitions[key]["unit"])}</dd></div>'
                   for key, number in values.items())


def results_table(fields, states, first, second, caption):
    rows = "".join(
        f'<tr><th scope="row">{safe(states[key]["label"])}</th>'
        f'<td>{quantity(first[key], states[key]["unit"])}</td>'
        f'<td>{quantity(second[key], states[key]["unit"])}</td></tr>'
        for key in fields)
    return (f'<table class="execution-table"><caption>{caption}</caption><thead><tr>'
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


def render_execution(item):
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
    comparisons = []
    for side, letter in (("first", "А"), ("second", "Б")):
        source = program[side]["source"]
        line = source["line"]
        comparisons.append(
            f'<div class="execution-side"><dt>Правило {letter} · '
            f'<a href="#source-line-{line}">строка {line}</a></dt>'
            f'<dd>{quantity(difference[side], definition["unit"])}</dd></div>')
    rules = "".join(
        f'<blockquote><span>Правило {letter} · <a href="#source-line-{side["source"]["line"]}">'
        f'строка {side["source"]["line"]}</a></span>{safe(side["source"]["quote"])}</blockquote>'
        for side, letter in ((program["first"], "А"), (program["second"], "Б")))
    full_trace = trace(program, "scenario", example, states, inputs)
    control = trace(program, "control", result["control"], states, inputs)
    event_inputs = values_list(step["inputs"], inputs)
    final = (results_table(program["observe"], states, example["final"]["first"], example["final"]["second"],
                           "Итог после последнего шага")
             if step["step"] < len(example["steps"]) else "")
    return (f'<div class="execution" id="execution-{fid}">'
            '<p class="execution-caption">Исполняемый пример</p>'
            f'<h4>{safe(definition["label"])}</h4>'
            f'<dl class="execution-comparison">{"".join(comparisons)}</dl>'
            f'<p class="execution-break"><b>Первое расхождение · шаг {step["step"]}</b>'
            f'<span>{safe(step["label"])}</span></p>'
            + (f'<dl class="execution-values execution-shared" aria-label="Общий вход этого шага">{event_inputs}</dl>'
               if event_inputs else "")
            + final
            + f'<details class="execution-trace"><summary>Входные данные и шаги расчёта</summary>{full_trace}</details>'
            f'<details class="execution-sources"><summary>Две цитаты из ТЗ</summary>{rules}</details>'
            f'<details class="execution-control"><summary>Контрольный пример · исходы совпали</summary>{control}</details>'
            '</div>')
