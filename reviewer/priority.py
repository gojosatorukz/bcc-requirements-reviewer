"""Versioned priority rubric for findings. Inference supplies evidence, never the rank."""

from .contract import INTERPRETATION, TEXT, record, validate_shape


POLICY = "impact-v1"
CONFLICT = "incompatible_product_outcomes"
DELIVERY = "implementation_or_contract"
EDITORIAL = "document_only"
RULES = {
    CONFLICT: ("критический", "Для одного допустимого сценария заданы два несовместимых результата продукта."),
    DELIVERY: ("существенный", "Требуется решение о реализации, контракте, границе поставки или приёмке."),
    EDITORIAL: ("редакционный", "Исправление меняет только оформление, терминологию или паспорт документа."),
}
ORDER = {"критический": 0, "существенный": 1, "редакционный": 2}
WITNESS = record(scenario=TEXT, first=INTERPRETATION, second=INTERPRETATION)
TRIAGE = record(rule={"type": "string", "enum": list(RULES)},
                witness={"type": "array", "items": WITNESS})

INSTRUCTIONS = """
Верни triage для каждой находки. Код назначит приоритет по следующей единой шкале.
• incompatible_product_outcomes: документ предписывает два взаимоисключающих
бизнес-результата при одинаковых допустимых условиях (например, состояние операции,
денег, доступа или видимый эффект действия). Это доказанное нарушение. В witness
дай ровно один общий конкретный сценарий, два разных исхода и для каждого его
дословное основание. Включи обе строки также в evidence самой находки.
• implementation_or_contract: недостающее правило или предусловие, неоднозначность,
проверяемость, интеграция, транспорт, среда, SLA либо нарушение технического стандарта.
Сюда входят записывающий безопасный HTTP-метод, несовместимые HTTP-коды, недостающий
метод, поле или пример ответа. Серьёзность гипотетического последствия не превращает
отклонение от стандарта в пару противоречащих бизнес-правил. Если при этих же условиях
документ отдельно предписывает взаимоисключающие бизнес-исходы, оформи это отдельно.
• document_only: только авторство, версия, название, оформление или синонимы;
можно исправить без изменения поведения, контракта, теста или комплекта поставки.
Если значение влияет на реализацию или ожидаемый результат, выбери правило выше.
Для последних двух правил witness — пустой массив. Выбирай правило по предмету
исправления и наблюдаемому результату. Уверенность, количество цитат, номер в списке
и порядок обнаружения не задают срочность."""


def derive_priority(finding, lines):
    triage = finding["triage"]
    validate_shape(triage, TRIAGE, "triage")
    rule, witnesses = triage["rule"], triage["witness"]
    if rule == CONFLICT:
        if finding["category"] != "нарушение" or len(witnesses) != 1:
            raise ValueError("Для конфликта продукта требуется нарушение и один общий сценарий с двумя исходами")
        witness = witnesses[0]
        quotes = []
        cited = {ref["line"] for ref in finding["evidence"]}
        for side in (witness["first"], witness["second"]):
            ref = side["source"]
            if ref["line"] not in cited or ref["quote"] not in lines.get(ref["line"], ""):
                raise ValueError(f"Основание исхода должно ссылаться на цитируемую строку ТЗ: {ref['line']}")
            quotes.append((ref["line"], ref["quote"].strip()))
        if quotes[0] == quotes[1] or witness["first"]["outcome"].strip() == witness["second"]["outcome"].strip():
            raise ValueError("Оба исхода и их основания должны различаться")
    elif witnesses:
        raise ValueError("Сценарий с двумя исходами заполняется только для конфликта продукта")
    return RULES[rule][0]
