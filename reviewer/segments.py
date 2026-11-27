"""Partition Markdown without discarding table headers, examples or source positions."""

import re


RULE = re.compile(r"(?:-{3,}|\*{3,}|_{3,})$")
TABLE_RULE = re.compile(r"\s*:?-{3,}:?\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


def is_table_rule(text):
    cells = text.strip().strip("|").split("|")
    return "|" in text and all(TABLE_RULE.fullmatch(cell) for cell in cells)


def segment(lines, pdf_json_blocks=()):
    native = {}
    previous = 0
    for span in pdf_json_blocks:
        if (type(span) is not dict or set(span) != {"start", "end"} or type(span["start"]) is not int
                or type(span["end"]) is not int or not previous < span["start"] <= span["end"] <= len(lines)):
            raise ValueError("Неверный диапазон печатного примера JSON в PDF")
        previous = native[span["start"]] = span["end"]
    blocks = []
    headings = []
    header = None
    i = 0
    while i < len(lines):
        start = i
        text = lines[i].strip()
        i += 1
        if not text:
            header = None
            continue
        kind = "текст"
        fence = FENCE.match(text)
        heading = HEADING.match(text)
        if start + 1 in native:
            kind = "пример json"
            i = native[start + 1]
            header = None
        elif fence:
            marker, language = fence.groups()
            kind = "пример " + (language.strip().lower() or "кода")
            while i < len(lines):
                closing = lines[i].strip()
                i += 1
                if len(closing) >= len(marker) and set(closing) == {marker[0]}:
                    break
            header = None
        elif heading:
            level, label = len(heading[1]), heading[2]
            headings = [(n, title) for n, title in headings if n < level] + [(level, label)]
            header = None
            kind = "заголовок"
        elif is_table_rule(text) or RULE.fullmatch(text):
            kind = "разметка"
        elif "|" in text and i < len(lines) and is_table_rule(lines[i]):
            header = start + 1
            kind = "шапка таблицы"
        elif "|" in text and header:
            kind = "строка таблицы"
        else:
            header = None
        block = {"start": start + 1, "end": i, "kind": kind,
                 "section": " / ".join(title for _, title in headings)}
        if header and kind == "строка таблицы":
            block["header"] = header
        blocks.append(block)
    return blocks
