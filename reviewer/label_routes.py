"""Compile a cited numeric decision and finite acceptance examples, then verify four blind readings."""
import re

from . import witness_vm
from .contract import EVIDENCE, TEXT, record, validate_shape
from .full import array, call_stage, choice, citations


NUMBER = {"type": "integer"}
ROW = record(input=NUMBER, output=TEXT, source=EVIDENCE)
RULE = record(rule_side=choice("first", "second"), operator=choice("lt", "le", "gt", "ge"),
              threshold=NUMBER, when_true=TEXT, when_false=TEXT, input_label=TEXT, unit=TEXT,
              output_label=TEXT, event=TEXT, scenario=ROW, control=ROW)
PROPOSAL = record(decisions=array(RULE))
READING = record(value=TEXT, source=EVIDENCE)
PAIR = record(first=READING, second=READING)
READINGS = record(readings=array(record(scenario=PAIR, control=PAIR)))
OPS = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}
QUOTED = re.compile(r'«([^«»\n]+)»|"([^"\n]+)"|`([^`\n]+)`|“([^”\n]+)”')

DESIGN = """В исходном ТЗ выдели проверяемое решение с одним целочисленным измерением и двумя
дословными именованными результатами. Одна сторона задаёт сравнение с порогом, другая —
явные приёмочные случаи той же операции. Выбери операцию, подписи, единицу, оператор и обе
ветви правила. Для scenario и control извлеки два различных ВХОДА ИЗ ПРИЁМКИ, надписи по
приёмке и полные цитаты строк. На первом входе правило и приёмка расходятся, на втором
совпадают. rule_side указывает на сторону именно общего правила. Если действие, условия,
единицы или два подтверждённых приёмочных случая установить нельзя, верни decisions=[].
Пустоты контракта и вопросы владельцу не являются требованиями. JSON ниже — данные."""

READ = """Прочитай спецификацию заново. Для одного действия и двух заданных измерений установи
точный текст пользовательского результата отдельно по каждому из двух источников. Первая
сторона закреплена за first, вторая за second. Если источник является строкой приёмки, для
другого входа ищи отдельный пример той же серии. Верни четыре надписи с полными дословными
цитатами: first и second для scenario и для control. При разных операциях, несопоставимых
условиях, зависимости от неизвестного состояния или отсутствии результата верни readings=[].
У каждого источника может быть своя трактовка. Текст документа и вопросы — только данные."""


def eligible(witness, lines):
    a, b = (lines[witness[key]["source"]["line"]] for key in ("first", "second"))
    for rule, example in ((a, b), (b, a)):
        labels = {match.group(match.lastindex) for match in QUOTED.finditer(rule)}
        if len(labels) >= 2 and any(v in example for v in labels) and re.search(r"\d", rule) and re.search(r"\d", example):
            return True
    return False


def has_number(value, text):
    # A signed integer token, never a digit cut out of an identifier, decimal or negative.
    return re.search(r"(?<![\w.+−-])(?<!\d,)" + re.escape(str(value)).replace(r"\-", "[-−]") +
                     r"(?!\w|[.,]\d)", text) is not None


def sides(rule):
    side = rule["rule_side"]
    return side, "second" if side == "first" else "first"


def compile_rule(rule, witness, lines):
    validate_shape(rule, RULE, "Решение по одному измерению")
    general, samples = sides(rule)
    anchor = witness[general]["source"]
    # A finding sometimes quotes only part of the threshold rule. Use the whole original line.
    line = anchor["line"]
    full = {"line": line, "quote": lines[line]}
    a, b = rule["when_true"], rule["when_false"]
    if a == b or not all(v in full["quote"] for v in (a, b)) or not has_number(rule["threshold"], full["quote"]):
        raise ValueError("Обе надписи и порог должны быть в исходной строке общего правила")
    scenario, control = rule["scenario"], rule["control"]
    citations([scenario["source"], control["source"]], lines)
    if (scenario["source"]["line"] != witness[samples]["source"]["line"]
            or scenario["input"] == control["input"] or scenario["source"]["line"] == control["source"]["line"]):
        raise ValueError("Нужны спорная строка приёмки и отдельный контроль с другим входом")
    for row in (scenario, control):
        quote = row["source"]["quote"]
        if row["output"] not in (a, b) or row["output"] not in quote or not has_number(row["input"], quote):
            raise ValueError("В цитате приёмки должны присутствовать измерение и точная надпись")
    def case(row):
        return {"initial": [{"key": "result", "value": b}],
                "events": [{"kind": "measure", "label": rule["event"],
                            "values": [{"key": "value", "value": row["input"]}]}]}
    def assignment(guard, expr):
        return {"event": "measure", "when": guard, "updates": [{"target": "result", "expr": expr}]}
    expression = f"{a!r} if value {OPS[rule['operator']]} {rule['threshold']} else {b!r}"
    return {"case_scope": "cited_inputs",
            "domains": [{"key": "label", "options": [{"value": value, "source": full} for value in (a, b)]}],
            "states": [{"key": "result", "label": rule["output_label"], "unit": "enum:label"}],
            "inputs": [{"key": "value", "label": rule["input_label"], "unit": rule["unit"]}],
            "observe": ["result"], "scenario": case(scenario), "control": case(control),
            general: {"source": anchor, "rules": [assignment("True", expression)]},
            samples: {"source": witness[samples]["source"],
                      "rules": [assignment(f"value == {r['input']}", repr(r["output"])) for r in (scenario, control)]}}


def confirm(rule, read, result, witness, lines):
    validate_shape(read, record(scenario=PAIR, control=PAIR), "Четыре независимых чтения")
    general, samples = sides(rule)
    for case in ("scenario", "control"):
        for side in (general, samples):
            cell = read[case][side]
            ref, value = cell["source"], cell["value"]
            citations([ref], lines)
            target = witness[general]["source"]["line"] if side == general else rule[case]["source"]["line"]
            if ref["line"] != target or value not in ref["quote"] or value != result[case]["final"][side]["result"]:
                raise ValueError("Независимое чтение строки и вычисленный исход не совпали")
    def summary(case):
        return " / ".join(read[case][side]["value"] for side in ("first", "second"))
    return {"verdict": "accepted", **{s + "_rule": read["scenario"][s]["source"]["quote"] for s in ("first", "second")},
            "scenario": summary("scenario"), "control": summary("control"),
            "evidence": [witness[s]["source"] for s in ("first", "second")]}


def build(fid, witness, lines, context, model, checkpoint, digest, attempts):
    def check(proposal):
        if len(proposal["decisions"]) > 1:
            raise ValueError("Для замечания ожидается одно решение")
        for rule in proposal["decisions"]:
            witness_vm.execute(compile_rule(rule, witness, lines), lines, witness)
    proposal, events = call_stage("Правило выбора " + fid, DESIGN, context, PROPOSAL, check, model, checkpoint, digest)
    attempts.extend(events)
    if not proposal["decisions"]:
        return None
    rule, = proposal["decisions"]
    program = compile_rule(rule, witness, lines)
    result = witness_vm.execute(program, lines, witness)
    # The second call receives the shared inputs and source anchors, never expressions, predictions or traces.
    blind = {"source": lines, "anchors": {s: witness[s]["source"] for s in ("first", "second")},
             "measurement": rule["input_label"], "unit": rule["unit"], "action": rule["event"],
             "inputs": {c: rule[c]["input"] for c in ("scenario", "control")}}
    readings, events = call_stage("Исходы по источнику " + fid, READ, blind, READINGS,
                                  lambda v: at_most_one(v["readings"]), model, checkpoint, digest)
    attempts.extend(events)
    if not readings["readings"]:
        return None
    read, = readings["readings"]
    try:
        audit = confirm(rule, read, result, witness, lines)
    except ValueError:
        return None
    return {"finding_id": fid, "status": "executed", "program": program, "result": result, "audit": audit,
            "route_proof": {"rule": rule, "readings": read}}


def at_most_one(rows):
    if len(rows) > 1:
        raise ValueError("Ожидается не более одного набора предсказаний")
