"""Replay source-linked interpretations with bounded integers and closed nominal domains."""

import ast
from dataclasses import dataclass
import keyword
import operator
import re

from .contract import EVIDENCE, TEXT, record, validate_shape


def _list(items):
    return {"type": "array", "items": items}


FIELD = record(key=TEXT, label=TEXT, unit=TEXT)
OPTION = record(value=TEXT, source=EVIDENCE)
DOMAIN = record(key=TEXT, options=_list(OPTION))
VALUE = record(key=TEXT, value={"type": ["integer", "string"]})
EVENT = record(kind=TEXT, label=TEXT, values=_list(VALUE))
CASE = record(initial=_list(VALUE), events=_list(EVENT))
UPDATE = record(target=TEXT, expr=TEXT)
RULE = record(event=TEXT, when=TEXT, updates=_list(UPDATE))
SIDE = record(source=EVIDENCE, rules=_list(RULE))
_PROGRAM_FIELDS = dict(states=_list(FIELD), inputs=_list(FIELD), observe=_list(TEXT), first=SIDE,
                       second=SIDE, scenario=CASE, control=CASE)
_OLD_PROGRAM = record(**_PROGRAM_FIELDS)
PROGRAM = record(domains=_list(DOMAIN), **_PROGRAM_FIELDS)
VERSION = "witness-v1"

LANGUAGE = """Составь общую исполняемую модель двух прочтений. Все обозначения и поля нужны для
данного бизнес-сценария. В states и inputs задай уникальный короткий ASCII key в snake_case,
понятную русскую подпись label и unit, например «₸», «push», «месяц» или «1» для множителя.
Для числовых полей используй точные целые числа; для дробных денег выбери меньшую общую единицу.
Если контракт возвращает именованный статус, используй исходную строку как результат.
Объяви закрытый тип в domains: key — имя типа, options — конечный список допустимых строк.
Каждый объект option содержит value и source (строка и дословная цитата с полным именем).
Для поля такого типа укажи unit="enum:имя_типа". Для программы только с числами domains=[].
Строки в initial и values — точные значения справочника. У результата и входа могут быть
разные справочники, включая фразы на языке источника. Не изобретай синонимы и коды.
Укажи все перечисленные в ТЗ варианты данного типа и реализуй все явно заданные переходы
используемого события, включая иные согласованные исходы. В выражении строку заключай в
кавычки. Допустимы ==, !=, присваивание и условный выбор значений одного типа по реальным
входным данным. Порядок, арифметика и сравнение разных справочников запрещены. Сначала
определи подтверждённое цитатой правило каждой стороны, затем запиши его вычисление.
Обе программы получают одни и те же initial (все состояния), затем общие события. Каждое
событие имеет kind (ASCII key), содержательный label и только входы этого события в values.
Правила для совпадающего event читают состояние ДО шага и входы текущего события. Присваивания
обновляют объявленные states одновременно; не обновлённое поле сохраняется. В одной трактовке
нельзя дважды записать в поле на одном шаге. Не указывай вычисленный ответ во входе.
when — логическая формула или True. expr — арифметическая целая формула: имена полей без
префиксов, целые литералы, +, -, *, // (целая часть вниз), %, скобки, min(a,b), max(a,b),
abs(a), x if условие else y. Сравнения == != < <= > >=, условия and/or/not. Никаких
вызовов функций, кроме min, max, abs. Корректно учитывай единицы. Например при when
«spent < limit and spent + amount >= limit» обновление «count + 1» считает пересечение.
Денежные результаты должны вычисляться из суммы во входе; не вшивай конкретный итог.
Если событие по правилу снимает блокировку целиком или обнуляет счётчик, допустимо обновление 0.
Укажи observe — одинаковые для обеих сторон измеримые поля состояния. scenario приводит к
расхождению, control — соседний пример, в котором расхождения нет НИ НА ОДНОМ шаге.
Используй те же виды событий в том же порядке и тот же набор входов в каждом событии
обеих последовательностей. Измени только необходимые входы либо начальные значения.
Делай сравнимый контроль, сохраняя смысл и допустимость событий. Не добавляй фиктивный флаг
«версия», «трактовка», «контроль», заранее выбранный ответ или заведомо искусственное условие.
В first.source и second.source скопируй соответствующие исходные цитаты свидетельства.
Если правила или предпосылки невозможно честно воспроизвести в этом языке, откажись от модели."""

_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}\Z", re.ASCII)
_MAX_INT = 2**53 - 1  # JSON numbers also round-trip exactly through browser clients.
_BOOL = object()
_LITERAL = object()
_SCALAR = "1"
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_COMPARISONS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCTIONS = {"min": min, "max": max, "abs": abs}


def _bounded(value, name="Значение"):
    if type(value) is not int or abs(value) > _MAX_INT:
        raise ValueError(f"{name}: нужно целое число в диапазоне ±{_MAX_INT}")
    return value


def _count(items, lo, hi, name):
    if not lo <= len(items) <= hi:
        raise ValueError(f"{name}: допустимо от {lo} до {hi} элементов")


def _short_text(text, maximum, name):
    if len(text) > maximum or text != text.strip() or any(ord(c) < 32 for c in text):
        raise ValueError(f"{name}: некорректное обозначение")


def _key(text):
    if not _KEY.fullmatch(text) or keyword.iskeyword(text) or text in _FUNCTIONS:
        raise ValueError(f"Недопустимое имя: {text[:40]}")
    return text


@dataclass(frozen=True)
class _Domain:
    key: str
    options: frozenset


@dataclass(frozen=True)
class _StringLiterals:
    # A conditional may select several literals before a field determines their nominal type.
    options: frozenset


def _cites_option(value, ref, lines):
    line = lines.get(ref["line"], "")
    quote = ref["quote"]
    # Test the word boundary in the full source. The citation itself may end mid-token.
    for candidate in re.finditer(re.escape(value), quote):
        pattern = (re.escape(quote[:candidate.start()]) + r"(?<![\w-])" + re.escape(value) +
                   r"(?![\w-])" + re.escape(quote[candidate.end():]))
        if re.search(pattern, line):
            return True
    return False


def _domains(rows, lines):
    _count(rows, 0, 12, "Справочники")
    domains = {}
    for row in rows:
        key = _key(row["key"])
        if key in domains:
            raise ValueError(f"Справочник {key} указан дважды")
        _count(row["options"], 1, 48, key)
        options = set()
        for option in row["options"]:
            value, ref = option["value"], option["source"]
            _short_text(value, 100, key)
            if value in options:
                raise ValueError(f"{key}: значение {value} указано дважды")
            if ref["line"] < 1 or not _cites_option(value, ref, lines):
                raise ValueError(f"{key}: исходная строка {ref['line']} не подтверждает точное значение {value!r}")
            options.add(value)
        domains[key] = _Domain(key, frozenset(options))
    return domains


def _fields(rows, name, minimum, domains):
    _count(rows, minimum, 24, name)
    fields = {}
    for row in rows:
        key = _key(row["key"])
        if key in fields:
            raise ValueError(f"{name}: повтор поля {key}")
        _short_text(row["label"], 120, key)
        _short_text(row["unit"], 32, key)
        unit = row["unit"]
        if domains is not None and unit.startswith("enum:"):
            domain = unit[5:]
            if domain not in domains:
                raise ValueError(f"{key}: неизвестный справочник {domain}")
            unit = domains[domain]
        fields[key] = unit
    return fields


def _values(rows, allowed, name, *, all_required=False):
    _count(rows, 0, len(allowed), name)
    values = {}
    for row in rows:
        key = row["key"]
        if key not in allowed or key in values:
            raise ValueError(f"{name}: неизвестное или повторное поле {key}")
        value, unit = row["value"], allowed[key]
        if isinstance(unit, _Domain):
            if type(value) is not str or value not in unit.options:
                raise ValueError(f"{key}: значение отсутствует в справочнике {unit.key}")
            values[key] = value
        else:
            values[key] = _bounded(value, key)
    if all_required and values.keys() != allowed.keys():
        raise ValueError(f"{name}: указаны не все начальные значения")
    return values


def _numeric(unit):
    if isinstance(unit, (_Domain, _StringLiterals)):
        raise ValueError("Арифметика над значениями справочника запрещена")
    if unit is _BOOL:
        raise ValueError("Нельзя использовать логическое значение как число")
    return unit


def _same_unit(left, right):
    if left == right:
        return left
    if isinstance(left, _StringLiterals):
        if isinstance(right, _StringLiterals):
            return _StringLiterals(left.options | right.options)
        if isinstance(right, _Domain) and left.options <= right.options:
            return right
        raise ValueError("Строка не принадлежит нужному справочнику")
    if isinstance(right, _StringLiterals):
        return _same_unit(right, left)
    if left is _LITERAL:
        return _numeric(right)
    if right is _LITERAL:
        return _numeric(left)
    raise ValueError("Несовместимые единицы измерения в выражении")


def _number_unit(op, left, right):
    _numeric(left)
    _numeric(right)
    if op in (ast.Add, ast.Sub, ast.Mod):
        return _same_unit(left, right)
    if op is ast.Mult:
        if left in (_LITERAL, _SCALAR):
            return right if right is not _LITERAL else left
        if right in (_LITERAL, _SCALAR):
            return left
        raise ValueError("У множителя должна быть единица 1")
    if right in (_SCALAR, _LITERAL):
        return left
    if left == right:
        return _SCALAR
    raise ValueError("Несовместимые единицы при делении")


def _boolean(unit):
    if unit is not _BOOL:
        raise ValueError("В условии требуется сравнение либо True/False")
    return unit


def _unit(node, fields):
    if isinstance(node, ast.Name):
        if node.id not in fields:
            raise ValueError(f"Нет такого состояния или входа в этом событии: {node.id}")
        return fields[node.id]
    if isinstance(node, ast.Constant):
        if type(node.value) is bool:
            return _BOOL
        if type(node.value) is str:
            _short_text(node.value, 100, "Строковый литерал")
            return _StringLiterals(frozenset((node.value,)))
        _bounded(node.value, "Литерал")
        return _LITERAL
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC:
        return _number_unit(type(node.op), _unit(node.left, fields), _unit(node.right, fields))
    if isinstance(node, ast.Compare) and all(type(op) in _COMPARISONS for op in node.ops):
        previous = _unit(node.left, fields)
        for op, other in zip(node.ops, node.comparators):
            current = _unit(other, fields)
            unit = _same_unit(previous, current)
            if isinstance(unit, _StringLiterals):
                raise ValueError("Сравнение строк требует поля конкретного справочника")
            if (unit is _BOOL or isinstance(unit, _Domain)) and not isinstance(op, (ast.Eq, ast.NotEq)):
                raise ValueError("Логические и именованные значения можно сравнивать только на равенство")
            previous = current
        return _BOOL
    if isinstance(node, ast.UnaryOp):
        unit = _unit(node.operand, fields)
        if isinstance(node.op, ast.Not):
            return _boolean(unit)
        if isinstance(node.op, (ast.UAdd, ast.USub)):
            return _numeric(unit)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for item in node.values:
            _boolean(_unit(item, fields))
        return _BOOL
    if isinstance(node, ast.IfExp):
        _boolean(_unit(node.test, fields))
        return _same_unit(_unit(node.body, fields), _unit(node.orelse, fields))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS
            and not node.keywords and len(node.args) == (1 if node.func.id == "abs" else 2)):
        units = [_numeric(_unit(arg, fields)) for arg in node.args]
        return units[0] if len(units) == 1 else _same_unit(*units)
    raise ValueError("Операция не поддерживается языком исполняемого примера")


def _read(node, values):
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        left, right = _read(node.left, values), _read(node.right, values)
        if isinstance(node.op, (ast.FloorDiv, ast.Mod)) and not right:
            raise ValueError("Деление на ноль в исполняемом примере")
        return _bounded(_ARITHMETIC[type(node.op)](left, right), "Промежуточный результат")
    if isinstance(node, ast.Compare):
        left = _read(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            right = _read(comparator, values)
            if not _COMPARISONS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.UnaryOp):
        value = _read(node.operand, values)
        if isinstance(node.op, ast.Not):
            return not value
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BoolOp):
        terms = (_read(child, values) for child in node.values)
        return all(terms) if isinstance(node.op, ast.And) else any(terms)
    if isinstance(node, ast.IfExp):
        return _read(node.body if _read(node.test, values) else node.orelse, values)
    return _FUNCTIONS[node.func.id](*(_read(arg, values) for arg in node.args))


def _parse(text, fields, expected):
    _short_text(text, 320, "Формула")
    try:
        node = ast.parse(text, mode="eval").body
    except (SyntaxError, RecursionError, ValueError) as exc:
        raise ValueError(f"Неверная формула: {text[:80]}") from exc
    if sum(1 for _ in ast.walk(node)) > 85:
        raise ValueError("Слишком сложная формула")
    _same_unit(_unit(node, fields), expected)
    return node


@dataclass(frozen=True)
class _Rule:
    condition: ast.AST
    updates: list


def _compile(side, event_fields, states, inputs):
    rules = {}
    _count(side["rules"], 1, 48, "Правила")
    for rule in side["rules"]:
        kind = rule["event"]
        if kind not in event_fields:
            raise ValueError(f"Отсутствует событие для правила {kind}")
        fields = {**states, **{key: inputs[key] for key in event_fields[kind]}}
        condition = _parse(rule["when"], fields, _BOOL)
        updates = []
        targets = set()
        _count(rule["updates"], 1, len(states), f"Обновления {kind}")
        for assignment in rule["updates"]:
            target = assignment["target"]
            if target not in states or target in targets:
                raise ValueError(f"Некорректное или повторное назначение {target}")
            targets.add(target)
            expression = _parse(assignment["expr"], fields, states[target])
            reset = isinstance(expression, ast.Constant) and type(expression.value) is int and expression.value == 0
            if not reset and not any(isinstance(n, ast.Name) and n.id in fields
                                     for subtree in (condition, expression) for n in ast.walk(subtree)):
                raise ValueError(f"{target}: безусловный готовый ответ нельзя выдать за вычисление")
            updates.append((target, expression))
        rules.setdefault(kind, []).append(_Rule(condition, updates))
    return rules


def _case(case, states, inputs, event_fields):
    initial = _values(case["initial"], states, "Исходное состояние", all_required=True)
    _count(case["events"], 1, 24, "События")
    events = []
    for event in case["events"]:
        kind = _key(event["kind"])
        _short_text(event["label"], 240, kind)
        values = _values(event["values"], inputs, kind)
        signature = frozenset(values)
        if event_fields.setdefault(kind, signature) != signature:
            raise ValueError(f"События {kind} должны принимать одинаковые входные поля")
        events.append((kind, event["label"], values))
    return initial, events


def _advance(state, inputs, rules):
    before = {**state, **inputs}
    updates = {}
    for rule in rules:
        if not _read(rule.condition, before):
            continue
        for target, expression in rule.updates:
            if target in updates:
                raise ValueError(f"Два сработавших правила обновляют поле {target} на одном шаге")
            updates[target] = _read(expression, before)
    return {**state, **updates}


class _Readings:
    """Collect only values actually read, including short-circuit and conditional expressions."""
    def __init__(self, context):
        self.context = context
        self.used = {}

    def __getitem__(self, name):
        self.used[name] = self.context[name]
        return self.used[name]


def _explain_field(state, inputs, rules, field):
    before = {**state, **inputs}
    reasons = []
    for rule in rules:
        assignments = [(target, expr) for target, expr in rule.updates if target == field]
        if not assignments:
            continue
        guard_inputs = _Readings(before)
        matched = _read(rule.condition, guard_inputs)
        changes = []
        for target, expr in assignments:
            operands = _Readings(before)
            after = _read(expr, operands) if matched else before[target]
            changes.append({"target": target, "expr": ast.unparse(expr), "reads": operands.used,
                            "before": before[target], "after": after, "applied": matched})
        reasons.append({"when": ast.unparse(rule.condition), "matched": matched, "reads": guard_inputs.used,
                        "updates": changes})
    return reasons


def _run(case, sides, observe, explanation=None, point=None):
    initial, events = case
    states = {name: initial.copy() for name in sides}
    start = {field: initial[field] for field in observe}
    trace = []
    first_difference = None
    for index, (kind, label, inputs) in enumerate(events, 1):
        before = states.copy()
        snapshot = {}
        for name, rules in sides.items():
            states[name] = _advance(before[name], inputs, rules.get(kind, ()))
            snapshot[name] = {field: states[name][field] for field in observe}
        fields = [key for key in observe if snapshot["first"][key] != snapshot["second"][key]]
        show = point[1] if point and point[0] == index else None
        if fields and first_difference is None:
            key = fields[0]
            first_difference = {"step": index, "field": key, "first": snapshot["first"][key],
                                "second": snapshot["second"][key]}
            if point is None:
                show = key
        if explanation is not None and show is not None:
            explanation.update(step=index, field=show, kind=kind, label=label)
            for name, rules in sides.items():
                explanation[name] = _explain_field(before[name], inputs, rules.get(kind, ()), show)
        trace.append({"step": index, "kind": kind, "label": label, "inputs": inputs, **snapshot, "different": fields})
    return {"initial": start, "steps": trace, "first_difference": first_difference, "final": snapshot}


def _evaluate(program, lines, witness, sample, explanation=None, point=None):
    schema = PROGRAM if isinstance(program, dict) and "domains" in program else _OLD_PROGRAM
    validate_shape(program, schema, "Исполняемый пример")
    domains = _domains(program["domains"], lines) if "domains" in program else None
    states = _fields(program["states"], "Состояния", 1, domains)
    inputs = _fields(program["inputs"], "Входы", 0, domains)
    used_domains = {unit.key for unit in (*states.values(), *inputs.values()) if isinstance(unit, _Domain)}
    if used_domains != (domains or {}).keys():
        raise ValueError("Заявлен справочник, который не используется в полях")
    if states.keys() & inputs.keys():
        raise ValueError("Имена состояний и входов должны различаться")
    observe = program["observe"]
    if not observe or len(set(observe)) != len(observe) or set(observe) - states.keys():
        raise ValueError("Нужен непустой список уникальных наблюдаемых состояний")
    event_fields = {}
    scenario = _case(program["scenario"], states, inputs, event_fields)
    control = _case(program["control"], states, inputs, event_fields)
    if point is not None and (type(point[0]) is not int or not 1 <= point[0] <= len(scenario[1])
                              or point[1] not in observe):
        raise ValueError("Для пояснения нужны существующий шаг и наблюдаемый показатель")
    if [event[0] for event in scenario[1]] != [event[0] for event in control[1]]:
        raise ValueError("В контроле нужны те же виды событий в том же порядке")
    if scenario[0] == control[0] and [e[2] for e in scenario[1]] == [e[2] for e in control[1]]:
        raise ValueError("В контрольном примере нужно изменить исходное условие или вход события")
    if set(inputs) != set().union(*event_fields.values()):
        raise ValueError("Заявлен вход, которого нет ни в одном событии")
    sides = {}
    for name in ("first", "second"):
        side = program[name]
        ref = side["source"]
        if ref["line"] < 1 or ref["quote"] not in lines.get(ref["line"], ""):
            raise ValueError(f"{name}: цитата правила не подтверждается в указанной строке")
        if witness is not None and ref != witness[name]["source"]:
            raise ValueError(f"{name}: требуется исходная цитата соответствующей стороны свидетельства")
        sides[name] = _compile(side, event_fields, states, inputs)
    if program["first"]["source"] == program["second"]["source"]:
        raise ValueError("Обе стороны ссылаются на одну и ту же цитату")
    replay = {"version": "witness-enum-v1" if domains else VERSION,
              "scenario": _run(scenario, sides, observe, explanation if sample is None else None, point),
              "control": _run(control, sides, observe)}
    if replay["scenario"]["first_difference"] is None:
        raise ValueError("В общем сценарии наблюдаемые результаты совпадают на всех шагах")
    if replay["control"]["first_difference"] is not None:
        raise ValueError("На контрольном примере наблюдаемые результаты тоже расходятся")
    if sample is not None:
        if [event["kind"] for event in sample["events"]] != [event[0] for event in scenario[1]]:
            raise ValueError("При повторном исполнении нужны прежние виды событий в том же порядке")
        return _run(_case(sample, states, inputs, event_fields), sides, observe, explanation, point)
    return replay


def execute(program, lines, witness=None):
    """Compute the witness and control. A valid witness must diverge and its control must agree."""
    return _evaluate(program, lines, witness, None)


def simulate(program, lines, case, witness=None):
    """Revalidate the original witness, then replay its rules on new inputs, including ones that agree."""
    validate_shape(case, CASE, "Тестовые входы")
    return _evaluate(program, lines, witness, case)


def explain_step(program, lines, witness=None, *, case=None, step=None, field=None):
    """Explain a selected step and field, or the first divergence when neither is specified."""
    if case is not None:
        validate_shape(case, CASE, "Тестовые входы")
    explanation = {}
    point = None if step is None and field is None else (step, field)
    _evaluate(program, lines, witness, case, explanation, point)
    return explanation or None
