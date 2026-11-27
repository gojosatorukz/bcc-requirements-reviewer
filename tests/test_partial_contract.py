from unittest import TestCase

from reviewer.json_examples import inspect_examples
from reviewer.partial_contract import contract_questions
from reviewer.segments import segment


def questions(declaration, caption, body, between=(), after=()):
    lines = ["# Calibration export", declaration, *between, caption, "```json", *body.splitlines(), "```", *after]
    blocks = segment(lines)
    return list(contract_questions(lines, blocks, inspect_examples(lines, blocks)))


class PartialContractTests(TestCase):
    def test_explicit_minimum_and_precise_source_for_multiline_object(self):
        findings = questions("A successful response must contain three required fields.",
                             "Successful response example:", '{\n  "wavelength": 532\n}')
        self.assertEqual(len(findings), 1)
        rule, _, question, evidence = findings[0]
        self.assertEqual(rule, "success_field_count")
        self.assertEqual(evidence, [2, 3, 6])
        self.assertIn("3", question)
        self.assertIn("«wavelength»", question)
        self.assertTrue(question.endswith("?"))
        corrected = '{"wavelength": 532, "grating": "G-2", "unit": "nm"}'
        self.assertEqual(questions("A successful response must contain three required fields.",
                                   "Successful response example:", corrected), [])

    def test_russian_successful_example_with_a_numeric_context_and_two_required_fields(self):
        assertion = "Сервис возвращает код лота и дату. В успешном ответе оба поля обязательны."
        caption = "Пример успешного ответа для партии из 35 семян"
        self.assertEqual(questions(assertion, caption, '{"lot":"q"}')[0][3], [2, 3, 5])
        self.assertEqual(questions(assertion, caption, '{"lot":"q","date":"2026-04-03"}'), [])
        self.assertEqual(len(questions(assertion, caption, '{}')), 1)
        self.assertEqual(questions(assertion, caption, '{"extra":3,"lot":"q"}'), [])

    def test_does_not_cross_a_new_section_or_intervening_operation(self):
        assertion = "Успешный ответ содержит три обязательных поля."
        for between in [("## Второй измеритель",), ("Для второй установки есть другой контракт.",),
                        ("```text", "POST /other", "```")]:
            with self.subTest(between=between):
                self.assertEqual(questions(assertion, "Пример успешного ответа", '{"nm": 532}', between), [])

    def test_excludes_unbound_or_conditional_obligations_examples_and_nonflat_shapes(self):
        caption = "Пример успешного ответа"
        assertion = "В успешном ответе оба поля обязательны."
        for declaration in ("В теле запроса оба поля обязательны.", "В успешном ответе оба поля не обязательны.",
                            "В успешном ответе оба поля обязательны, если включён аудит.",
                            "В успешном ответе поля обязательны.", "В объекте датчика оба поля обязательны.",
                            "Если запрошена детализация. В успешном ответе оба поля обязательны."):
            with self.subTest(declaration=declaration):
                self.assertEqual(questions(declaration, caption, '{"key":42}'), [])
        for title in ("Пример ошибочного ответа", "Пример запроса", "Фрагмент успешного ответа",
                      "Пример успешного ответа для GET /v2/measurements", "Пример успешного ответа для ошибок 404"):
            with self.subTest(title=title):
                self.assertEqual(questions(assertion, title, '{"key":42}'), [])
        for body in ('{"measurement":{"nm":532,"unit":"nm"}}', '{"items":[1,2]}',
                     '[{"reading":3}]', 'null', '{"invalid":}'):
            with self.subTest(body=body):
                self.assertEqual(questions(assertion, caption, body), [])
        self.assertEqual(questions(assertion, caption, '{"nm":532}', after=("Часть полей опущена для краткости.",)), [])
