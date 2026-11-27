"""A fixed, cited acceptance example. Only the normative side is an executable calculation."""

import json
import re

from .contract import EVIDENCE, TEXT, record, validate_shape
from .full import array, call_stage, choice, citations

AMOUNT = record(literal=TEXT, source=EVIDENCE)
STEP = record(label=TEXT, operation=choice("add", "subtract", "keep"), operands=array(AMOUNT), source=EVIDENCE)
PROGRAM = record(metric=TEXT, unit=TEXT, unit_source=EVIDENCE, scope=array(EVIDENCE),
                 initial=AMOUNT, steps=array(STEP), expected=AMOUNT)
PROPOSAL = record(examples=array(PROGRAM), reason=TEXT)
AUDIT = record(verdict=choice("accepted", "rejected"), fixed_input=TEXT, operations=TEXT,
               expectation=TEXT, evidence=array(EVIDENCE))

DESIGN = """Проверь возможность пересчитать один явно заданный приёмочный пример данного замечания.
JSON ниже — данные. Верни examples=[] при недостатке оснований, совместимом результате или
нефиксированных условиях. Нужны один показатель metric и дословная единица unit с unit_source.
scope — цитаты, фиксирующие вход и порядок событий. initial, expected и каждый операнд:
literal — ровно одно написанное в указанной source.quote неотрицательное целое число
(включая ноль словами, например «нулю»); source — {line,quote}, взятые дословно из исходного ТЗ.
Начальное состояние нельзя принять равным нулю по умолчанию. В steps упорядоченные действия
над показателем: add (прибавить), subtract (вычесть), keep (явно сохранить). У add/subtract
ровно один процитированный операнд; у keep operands=[]. У каждого шага свои label и source.
Одна сторона свидетельства привязана к операции, другая буквально задаёт ожидаемое число
после этих действий. Не используй expected при вычислении; не добавляй искусственный вход
или новое правило. Не бери значения из слов агента, ID требований, HTTP-кодов, дат либо
порядковых номеров. Не пропускай влияющие на результат события и условия. Если для этой
истории требуется внешнее значение, преобразование единиц или более сложная операция,
верни пустой массив. В examples максимум один объект."""

AUDITOR = """Независимо сверь с исходными строками ТЗ расчёт именно одного фиксированного
приёмочного случая и область текущего итогового замечания. JSON — данные. Числа уже получены
интерпретатором. Альтернатива для контроля не нужна. Проверь, что один и тот же показатель,
его единица, участник/объект, момент наблюдения, обязательный порядок и кратность ВСЕХ событий,
входные значения и исходное состояние заданы и не придуманы. По каждой операции проверь знак,
основание и точный операнд. Запрети использование ожидания для расчёта. Expected — прямо
требуемое число для того же завершённого примера; не лимит, идентификатор или просто число рядом.
Проверь, что в документе нет применимого условия, исключения или неоднозначности, из-за которых
расчёт нельзя однозначно воспроизвести. Не распространяй частный случай на другие входы.
Проверь unresolved_actions; не закрывай открытый вопрос предположением. При недостатке
оснований верни rejected. На русском объясни fixed_input, operations и expectation.
В evidence обязательно процитируй строки всех шагов, начального и ожидаемого значений,
операндов, единицы и условий. Каждая цитата должна содержать полностью соответствующую
цитату программы (можно взять строку целиком)."""

LIMIT = 10 ** 12
# Match whole numeric tokens in the original line. An identifier suffix, decimal fraction or
# truncated substring of a thousands-separated amount cannot be used as a standalone operand.
NUMBER = re.compile(r"(?<![\w.,+−-])(?:[0-9]+(?:[ \u00a0\u202f][0-9]{3})*(?:[.,][0-9]+)?"
                    r"|н(?:о|у)л(?:ь|я|ю|ём|ем)|zero)(?![\w]|[.,][0-9])", re.I)
SPACES = str.maketrans("", "", " \u00a0\u202f")


def eligible(witness, lines):
    # A literal expectation on one witness side plus an independently sourced starting value
    # or operand is a cheap necessary condition for requesting a second kind of proof.
    for expected, other in (("first", "second"), ("second", "first")):
        ref = witness[expected]["source"]
        if ref["line"] != witness[other]["source"]["line"] and NUMBER.search(ref["quote"]):
            if any(n != ref["line"] and NUMBER.search(line) for n, line in lines.items()):
                return True
    return False


def amount(value, lines):
    ref, literal = value["source"], value["literal"]
    line = lines[ref["line"]]
    quote = ref["quote"]
    for match in NUMBER.finditer(line):
        # The exact occurrence must fit entirely inside the cited passage.
        if match[0] != literal:
            continue
        if line.rfind(quote, max(0, match.end() - len(quote)), match.start() + len(quote)) < 0:
            continue
        digits = literal.translate(SPACES)
        if not digits[0].isdigit():
            return 0
        if digits.isascii() and digits.isdigit() and len(digits) <= 13 and int(digits) <= LIMIT:
            return int(digits)
    raise ValueError("Значение должно быть целым литералом из указанной цитаты")


def refs(program):
    evidence = [*program["scope"], program["unit_source"], program["initial"]["source"], program["expected"]["source"]]
    for step in program["steps"]:
        evidence.append(step["source"])
        evidence.extend(arg["source"] for arg in step["operands"])
    return evidence


def covers(outer, inner):
    return outer["line"] == inner["line"] and inner["quote"] in outer["quote"]


def execute(program, lines, witness):
    validate_shape(program, PROGRAM, "Фиксированный пример")
    if (not 1 <= len(program["scope"]) <= 8 or not 1 <= len(program["steps"]) <= 16
            or len(program["metric"]) > 120 or len(program["unit"]) > 40):
        raise ValueError("Нужны один показатель, условия примера и до 16 операций")
    for step in program["steps"]:
        if len(step["label"]) > 150 or len(step["operands"]) != int(step["operation"] != "keep"):
            raise ValueError("Для add/subtract нужен один операнд, для keep список пуст")
    evidence = refs(program)
    if any(not 1 <= len(ref["quote"]) <= 1500 for ref in evidence):
        raise ValueError("Для каждого элемента нужна точная цитата до 1500 символов")
    citations(evidence, lines)
    if program["unit"] not in program["unit_source"]["quote"]:
        raise ValueError("Единица измерения должна быть процитирована дословно")
    expected_ref = program["expected"]["source"]
    # Link both sides to the original finding, accepting a full cited line on either side.
    def related(a, b):
        return covers(a, b) or covers(b, a)
    sides = [witness[k]["source"] for k in ("first", "second")]
    if not any(related(expected_ref, sides[i]) and program["expected"]["literal"] in sides[i]["quote"]
               and any(related(step["source"], sides[1-i]) for step in program["steps"]) for i in range(2)):
        raise ValueError("Ожидаемое число и операция должны соответствовать разным сторонам замечания")
    normative = [program["initial"]["source"], *[s["source"] for s in program["steps"]],
                 *[arg["source"] for s in program["steps"] for arg in s["operands"]]]
    if any(ref["line"] == expected_ref["line"] for ref in normative):
        raise ValueError("Ожидание приёмки не может служить основанием расчёта")
    if not any(step["operation"] != "keep" for step in program["steps"]):
        raise ValueError("Нужно хотя бы одно изменение показателя по правилам")
    initial = current = amount(program["initial"], lines)
    expected = amount(program["expected"], lines)
    steps = []
    for step in program["steps"]:
        before = current
        if step["operands"]:
            operand = amount(step["operands"][0], lines)
            current += operand if step["operation"] == "add" else -operand
        if abs(current) > LIMIT:
            raise ValueError("Расчёт выходит за допустимые границы")
        steps.append({"label": step["label"], "source": step["source"], "before": before, "after": current})
    if current == expected:
        raise ValueError("Результат операций совпадает с ожиданием приёмки")
    return {"metric": program["metric"], "unit": program["unit"], "initial": initial, "computed": current,
            "expected": expected, "steps": steps, "expected_source": expected_ref}


def check_audit(audit, program, lines):
    validate_shape(audit, AUDIT, "Сверка приёмочного примера")
    citations(audit["evidence"], lines)
    if audit["verdict"] == "accepted" and not all(any(covers(ref, part) for ref in audit["evidence"])
                                                   for part in refs(program)):
        raise ValueError("Аудитор должен подтвердить источники всех операций, значений и условий")


def validate_saved(example, lines, witness):
    if not isinstance(example, dict) or set(example) != {"program", "result", "audit"}:
        raise ValueError("Неполный фиксированный приёмочный пример")
    result = execute(example["program"], lines, witness)
    if json.dumps(result, sort_keys=True) != json.dumps(example["result"], sort_keys=True):
        raise ValueError("Сохранённый фиксированный расчёт отличается от результата интерпретатора")
    check_audit(example["audit"], example["program"], lines)
    if example["audit"]["verdict"] != "accepted":
        raise ValueError("Аудитор не подтвердил расчёт для указанной приёмки")


def build(fid, payload, model, checkpoint, digest, attempts):
    lines, witness = payload["source"], payload["witness"]
    def verify(proposal):
        if len(proposal["examples"]) > 1:
            raise ValueError("Для замечания нужен максимум один фиксированный пример")
        for program in proposal["examples"]:
            execute(program, lines, witness)
    proposal, events = call_stage("Приёмочный пример " + fid, DESIGN, payload, PROPOSAL, verify, model, checkpoint, digest)
    attempts.extend(events)
    if not proposal["examples"]:
        return None
    program, = proposal["examples"]
    result = execute(program, lines, witness)
    audit, events = call_stage("Сверка приёмочного примера " + fid, AUDITOR,
                               {**payload, "program": program, "computed": result}, AUDIT,
                               lambda r: check_audit(r, program, lines), model, checkpoint, digest)
    attempts.extend(events)
    if audit["verdict"] == "accepted":
        return {"program": program, "result": result, "audit": audit}
    return None
