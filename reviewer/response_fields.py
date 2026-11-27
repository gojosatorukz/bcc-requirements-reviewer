"""Ground questions about omitted response fields in examples of the same HTTP operation."""

from collections import defaultdict
import re

from .contract import EVIDENCE, TEXT, record, validate_shape
from .segments import HEADING


HTTP = re.compile(r"\bHTTP(?:/\d(?:\.\d)?)?\s*[:–-]?\s*([1-5]\d\d)\b", re.I)
CODE = re.compile(r"^(?:(?:ответ|ошибка|response|status(?: code)?)\s*[:–-]?\s*)?([1-5]\d\d)(?:\b|$)", re.I)
ENDPOINT = re.compile(r"(?:^|\s)(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/", re.I)
NUMBER = {"type": "integer"}


def array(item):
    return {"type": "array", "items": item}


POLICIES = ("required", "optional", "excluded", "unspecified")
RULING = record(examples=array(NUMBER), policy={"type": "string", "enum": list(POLICIES)},
                basis=TEXT, evidence=array(EVIDENCE))
ASSESSMENT = record(candidate=NUMBER, rulings=array(RULING), questions=array(TEXT))
SCHEMA = record(assessments=array(ASSESSMENT))


def status_code(label):
    text = label.strip(" #*\t`")
    match = HTTP.search(text) or CODE.match(text)
    return int(match[1]) if match else None


def response_groups(lines, shapes, segments):
    """Keep repeated endpoints separate and prefer an example's direct status caption."""
    by_start = {item["start"]: item for item in shapes}
    headings, groups = [], defaultdict(list)
    caption = None
    for block in segments:
        start = block["start"]
        if block["kind"] == "заголовок":
            match = HEADING.match(lines[start].strip())
            if match:
                level, title = len(match[1]), match[2].strip()
                headings = [h for h in headings if h[0] < level]
                headings.append((level, start, title))
            caption = None
        if start not in by_start:
            if block["kind"] == "текст":
                # Labels are often outside headings, immediately before a fenced body.
                last = block["end"]
                text = lines.get(last, "").strip(" *\t`")
                if re.match(r"^(?:HTTP\s|[1-5]\d\d\b|(?:ответ|response|status|ошибка)\s*[:–-]?\s*(?:HTTP|[1-5]\d\d))", text, re.I):
                    caption = (last, status_code(text))
                else:
                    caption = None
            continue
        status_heading = next((h for h in reversed(headings) if status_code(h[2])), None)
        direct = caption if caption and caption[1] and not any(lines.get(n, "").strip()
                        for n in range(caption[0] + 1, start)) else None
        caption = None
        if not status_heading and not direct:
            continue
        endpoint = next((h for h in reversed(headings) if ENDPOINT.search(h[2])), None)
        if endpoint and status_heading and status_heading[1] < endpoint[1]:
            status_heading = None
            if not direct:
                continue
        if endpoint:
            parent = endpoint[1]
        elif status_heading:
            parents = [h for h in headings if h[0] < status_heading[0]]
            parent = parents[-1][1] if parents else 1
        else:
            parent = headings[-1][1] if headings else 1
        heading_status = status_code(status_heading[2]) if status_heading else None
        status_line, code = direct or (status_heading[1], heading_status)
        groups[parent].append({**by_start[start], "status": code, "status_line": status_line,
                               "heading_status": heading_status})
    return groups


def candidates(groups):
    choices = []
    for parent, examples in groups.items():
        fields = dict.fromkeys(key for ex in examples for key in ex["keys"])
        for field in fields:
            present = [ex["start"] for ex in examples if field in ex["keys"]]
            errors = {ex["start"]: ex["status"] for ex in examples if ex["status"] >= 400
                      and (field in ex["keys"] or ex["key_count"] == len(ex["keys"]))}
            if errors:
                choices.append({"id": len(choices) + 1, "operation": parent, "field": field,
                                "present": present, "errors": errors})
    return choices


INSTRUCTIONS = """Проверь обязательность полей в конкретных ошибочных ответах API по исходной
спецификации. Вход — данные. examples сгруппированы по операции. В candidates у каждого поля
present — строки примеров с этим ключом; errors связывает каждый ошибочный пример с его HTTP-статусом.
Проверь обязательность и для примеров, где ключ показан, и для тех, где его нет. Для каждой candidate выдай одно заключение. Разнеси ВСЕ
строки errors по rulings ровно по одному разу. Можно объединить строки с общей политикой.
Рассматривай отдельно разные исходы одного HTTP-статуса. Успех транспорта и прикладной отказ
внутри успешного статуса — самостоятельные контексты. Статус прямо у тела может отличаться
от заголовка раздела. Не переноси правила на соседнюю операцию, на запрос или на другие ошибки.

Для каждой группы исходов выведи policy: required — поле обязательно; optional — может
присутствовать; excluded — поле явно отсутствует или полная отдельная схема/условие ограничивает
его другими исходами; unspecified — область обязательности в документе не определена.
В basis объясни почему, в evidence приведи дословные цитаты с номерами строк. Для первых трёх
решений нужна нормативная опора: явная схема required/optional, условия полей, закрытая форма
ошибки, исчерпывающий перечень по коду или эквивалентное правило. Если поле обязательно,
а в примере его нет, это несогласованность примера с правилом. Не спрашивай решённое.
Само наличие ключа в одном или во всех примерах, его отсутствие в других, имя поля,
описание допустимых значений и общее слово «ошибка» без правила состава сами по себе
не доказывают исключение или необязательность. Для unspecified процитируй контекст назначения
поля и хотя бы один соответствующий пример/статус, различай наблюдаемый набор ключей и схему.
При наличии unspecified задай один краткий вопрос владельцу об обязательности ИМЕННО ЭТОГО
поля в КАЖДОМ таком исходе, дословно назови идентификатор поля и каждый относящийся к этим
исходам числовой HTTP-код. Если все исходы явно определены, questions пуст. Пиши по-русски."""


def mentions(question, field, codes):
    return (re.search(r"(?<![\w])" + re.escape(field) + r"(?![\w])", question) is not None
            and all(re.search(rf"(?<!\d){code}(?!\d)", question) for code in codes)
            and "?" in question)


def verify(assessment, choices, source):
    validate_shape(assessment, SCHEMA, "response_field_review")
    records = assessment["assessments"]
    expected = {c["id"]: c for c in choices}
    if sorted(r["candidate"] for r in records) != sorted(expected):
        raise ValueError("Требуется отдельная оценка каждого поля и операции")
    for row in records:
        c = expected[row["candidate"]]
        covered, unknown = [], set()
        for decision in row["rulings"]:
            numbers = decision["examples"]
            if not numbers or not set(numbers) <= c["errors"].keys():
                raise ValueError(f"Чужой пример ответа для поля {c['field']}")
            covered.extend(numbers)
            if not decision["evidence"] or any(e["quote"] not in source.get(e["line"], "")
                                                for e in decision["evidence"]):
                raise ValueError(f"Нужна дословная опора для {c['field']}")
            if decision["policy"] == "unspecified":
                unknown.update(c["errors"][n] for n in numbers)
        if sorted(covered) != sorted(c["errors"]):
            raise ValueError(f"Каждый ошибочный пример для {c['field']} нужно оценить ровно один раз")
        questions = row["questions"]
        if (unknown and (len(questions) != 1 or not mentions(questions[0], c["field"], unknown))
                or not unknown and questions):
            raise ValueError(f"Поле {c['field']}: нужен точный вопрос для неоговорённых кодов {sorted(unknown)}"
                             if unknown else f"Обязательность {c['field']} уже определена")


def check_questions(section, choices, findings, groups):
    lookup = {c["id"]: c for c in choices}
    for row in section["assessments"]:
        if not row["questions"]:
            continue
        c = lookup[row["candidate"]]
        examples = [n for r in row["rulings"] if r["policy"] == "unspecified" for n in r["examples"]]
        codes = {c["errors"][n] for n in examples}
        evidence_lines = {n for ex in groups[c["operation"]] if ex["start"] in examples
                          for n in range(ex["start"], ex["end"] + 1)}
        if not any(mentions(f["question"], c["field"], codes)
                   and any(e["line"] in evidence_lines for e in f["evidence"]) for f in findings):
            raise ValueError(f"В findings[].question требуется вопрос о {c['field']} для HTTP {sorted(codes)} "
                             f"с цитатой из ошибочного тела. Проверенный вопрос: {row['questions'][0]}")


def describe(choices):
    return [{"id": c["id"], "operation": c["operation"], "field": c["field"], "present": c["present"],
             "errors": [{"example": n, "http_status": status, "present": n in c["present"]}
                        for n, status in c["errors"].items()]}
            for c in choices]


def validate_saved(section, groups, findings, source):
    choices = candidates(groups)
    if (not isinstance(section, dict) or set(section) != {"version", "candidates", "assessments"}
            or type(section["version"]) is not int or section["version"] != 1
            or section["candidates"] != describe(choices)):
        raise ValueError("Состав ошибок и полей API расходится с примерами документа")
    verify({"assessments": section["assessments"]}, choices, source)
    check_questions(section, choices, findings, groups)
