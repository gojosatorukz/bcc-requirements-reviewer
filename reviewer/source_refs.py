"""Transmit source anchors in the hot stages and expand exact quotes before validation."""

from .contract import EVIDENCE


def references(source):
    refs, extra = {}, {}
    next_id = max(source, default=0) + 1
    for line, text in source.items():
        if len(text) <= 512:
            refs[line] = {"line": line, "quote": text}
            continue
        # Long prose and minified examples need bounded, independently selectable excerpts.
        start = 0
        while start < len(text):
            stop = min(start + 300, len(text))
            if stop < len(text):
                split = text.rfind(" ", start + 120, stop)
                if split != -1:
                    stop = split + 1
            quote = text[start:stop].strip()
            if quote:
                refs[next_id] = {"line": line, "quote": quote}
                extra[next_id] = refs[next_id]
                next_id += 1
            start = stop
    return refs, extra


def wire_schema(schema):
    """Only evidence fields use IDs. Interpretations retain their own precise source quotes."""
    if schema["type"] == "array":
        return {**schema, "items": wire_schema(schema["items"])}
    if schema["type"] != "object":
        return schema
    properties = {}
    for key, spec in schema["properties"].items():
        if key == "evidence" and spec == EVIDENCE:
            properties[key] = {"type": "integer"}
        elif key == "evidence" and spec.get("items") == EVIDENCE:
            properties[key] = {"type": "array", "items": {"type": "integer"}}
        else:
            properties[key] = wire_schema(spec)
    return {**schema, "properties": properties}


def expand(value, refs):
    def resolve(anchor):
        if type(anchor) is not int or anchor not in refs:
            raise ValueError(f"Нет исходной строки или фрагмента для evidence={anchor}")
        return dict(refs[anchor])

    if isinstance(value, list):
        return [expand(item, refs) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "evidence":
                result[key] = [resolve(n) for n in item] if isinstance(item, list) else resolve(item)
            else:
                result[key] = expand(item, refs)
        return result
    return value


INSTRUCTIONS = """
Формат evidence в этой стадии — целый номер исходной строки либо массив таких номеров,
как указано в схеме. Цитаты по этим номерам программа извлечёт дословно сама. Для длинной
строки в source_fragments даны отдельные номера фрагментов: выбирай номер нужного
фрагмента вместо номера длинной строки. Вложенный witness.source по-прежнему требует
номер реальной строки и точную короткую цитату. Пиши ёмко: одно конкретное основание,
следствие и действие в соответствующих полях. Сохрани отличия исходов, единицы и коды.
"""
