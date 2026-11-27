"""Ask about visibly incomplete, explicitly labelled successful JSON responses."""

import json
import re

from .json_examples import example_body


COUNTS = {"оба": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
          "both": 2, "two": 2, "three": 3, "four": 4, "five": 5}
NUMBER = r"(?P<count>[1-9]\d?|оба|два|три|четыре|пять|both|two|three|four|five)"
FIELDS = r"(?:поле|поля|полей|ключа|ключей)"
DECLARATIONS = tuple(re.compile(pattern, re.I) for pattern in (
    rf"в\s+(?:каждом\s+)?успешном\s+ответе\s+{NUMBER}\s+{FIELDS}\s+обязательны",
    rf"в\s+(?:каждом\s+)?успешном\s+ответе\s+обязательны\s+{NUMBER}\s+{FIELDS}",
    rf"успешный\s+ответ\s+(?:содержит|должен\s+содержать)\s+{NUMBER}\s+обязательных\s+{FIELDS}",
    rf"(?:a |the |each |every )?successful (?:json )?response (?:contains|requires|must contain) {NUMBER} required (?:fields|keys)",
    rf"in (?:a |the |each |every )?successful response[ ,]+{NUMBER} (?:fields|keys) are (?:required|mandatory)",
))
CAPTION = re.compile(
    r"(?:пример\s+(?:полного\s+)?успешного\s+ответа|успешный\s+ответ"
    r"|(?:example\s+of\s+(?:a\s+)?)?successful\s+response(?:\s+example)?)"
    r"(?:\s+(?:для|for)\s+(?=[^.!?:]{0,75}\d)[^.!?:]{1,100})?\s*[:.]?", re.I)
LIMITATION = re.compile(
    r"\b(?:если|кроме|иногда|только|без|может|могут|не|вложенн\w*|частичн\w*|фрагмент\w*"
    r"|сокращ\w*|запрос\w*|ошиб\w*|заголовк\w*|вариант\w*|схем\w*"
    r"|if|when|unless|except|only|may|not|nested|partial|excerpt|schema|request|error|headers?)\b"
    r"|(?:^|\s)/[\w-]|https?://", re.I)
ABBREVIATED = re.compile(r"\b(?:для краткости|сокращ[её]н\w*|опущен\w*|показан\w* только"
                         r"|omitted|abridged|for brevity|only .* shown)\b", re.I)


def required_count(text):
    if LIMITATION.search(text):
        return None
    # The final sentence must assert the count for the successful response itself.
    # Earlier prose may introduce the operation or name its fields.
    sentence = re.split(r"[.!?]\s+", text.strip().rstrip(".!"))[-1]
    for pattern in DECLARATIONS:
        if match := pattern.fullmatch(sentence):
            number = match["count"].casefold()
            return int(number) if number.isdecimal() else COUNTS[number]
    return None


def contract_questions(lines, blocks, checks):
    valid = {item["start"] for item in checks["examples"] if item["status"] == "valid"}
    for index, example in enumerate(blocks):
        if index < 2 or example["start"] not in valid:
            continue
        declaration, caption = blocks[index - 2:index]
        # Adjacent prose in the same section binds the declaration and its example.
        if (declaration["kind"] != "текст" or caption["kind"] != "текст"
                or declaration["section"] != example["section"] or caption["section"] != example["section"]):
            continue
        caption_text = lines[caption["start"] - 1].strip()
        if not CAPTION.fullmatch(caption_text) or LIMITATION.search(caption_text):
            continue
        if index + 1 < len(blocks):
            following = blocks[index + 1]
            if following["kind"] == "текст" and ABBREVIATED.search(lines[following["start"] - 1]):
                continue
        count = required_count(lines[declaration["start"] - 1])
        if count is None:
            continue
        first, body = example_body(lines, example["start"], example["end"])
        value = json.loads(body, parse_int=str, parse_float=str)
        if (not isinstance(value, dict) or len(value) >= count
                or any(isinstance(v, (list, dict)) for v in value.values())):
            continue
        # Cite a key in the object, or the object itself when it is empty.
        body_lines = body.splitlines()
        nonblank = [(i, line) for i, line in enumerate(body_lines) if line.strip()]
        offset = next((i for i, line in nonblank if '"' in line), nonblank[0][0])
        shown = " Показан ключ «" + next(iter(value))[:64] + "»." if len(value) == 1 else ""
        yield ("success_field_count", "Уточните состав успешного ответа API",
               f"В контракте указано число обязательных полей — {count}, в примере показано {len(value)}.{shown} "
               "Какие JSON-ключи и значения должен содержать полный успешный ответ?",
               [declaration["start"], caption["start"], first + offset])
