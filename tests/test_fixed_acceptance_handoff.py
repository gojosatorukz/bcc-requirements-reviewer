import csv
from io import StringIO
from unittest import TestCase
from xml.etree import ElementTree

from reviewer.acceptance_example import execute
from reviewer.execution_view import render_execution
from reviewer.full_render import decision_brief, finding_markdown
from reviewer.jira_export import issues_csv


class FixedAcceptanceHandoffTests(TestCase):
    def setUp(self):
        texts = [
            "Один резерв и одна отмена для <A>.",
            "Остаток до резерва равен 40 шт.",
            "Размер резерва фиксирован: 7 шт.",
            "Вычесть размер резерва при подтверждении.",
            "После отмены остаток сохраняется.",
            "После отмены ожидается 39 шт.",
        ]
        refs = [{"line": n, "quote": quote} for n, quote in enumerate(texts, 1)]
        scope, start, operand, rule, keep, expected = refs
        def value(literal, source):
            return {"literal": literal, "source": source}
        program = {"metric": "Остаток <A>", "unit": "шт.", "unit_source": start, "scope": [scope],
                   "initial": value("40", start), "expected": value("39", expected),
                   "steps": [{"label": "Резерв & списание", "operation": "subtract",
                              "operands": [value("7", operand)], "source": rule},
                             {"label": "Отмена", "operation": "keep", "operands": [], "source": keep}]}
        witness = {"first": {"source": rule}, "second": {"source": expected}}
        result = execute(program, {r["line"]: r["quote"] for r in refs}, witness)
        self.item = {"finding_id": "D-01", "status": "unsupported", "reason": "Описана только одна отмена.",
                     "acceptance_example": {"program": program, "result": result, "audit": {}}}
        self.finding = {"id": "D-01", "category": "нарушение", "priority": "критический",
                        "title": "Итог отмены", "reason": "Исходы различаются.", "owner": "Владелец резерва",
                        "question": "Какой остаток должен увидеть клиент после отмены?",
                        "action": "Согласовать остаток.", "evidence": [rule, expected]}
        self.run = {"input_name": "Резерв.md", "input_sha256": "a" * 64}
        self.report = {"run": self.run, "executables": {"items": [self.item]}, "whole": {"findings": [self.finding]}}
        self.refs = refs

    def test_workbench_offline_panel_and_short_brief_show_a_calculation_and_a_literal_expectation(self):
        panel = ElementTree.fromstring(render_execution(self.item))
        self.assertEqual(panel.get("id"), "execution-D-01")
        self.assertEqual(panel.get("data-kind"), "fixed-acceptance")
        self.assertEqual([n.text for n in panel.findall("dl/div/dt")], ["Расчёт по правилам ТЗ", "Ожидание из приёмки"])
        self.assertEqual([n.text for n in panel.findall("dl/div/dd")], ["33 шт.", "39 шт."])
        self.assertIsNone(panel.find(".//A"))
        self.assertNotIn("Контрольный пример", "".join(panel.itertext()))
        for ref in self.refs:
            anchor = panel.find(f'.//a[@href="#source-line-{ref["line"]}"]')
            self.assertIsNotNone(anchor)
            self.assertIn(ref["quote"], "".join(panel.itertext()))
        self.assertIn("40 шт. − 7 шт. = 33 шт.", "".join(panel.itertext()))
        self.assertIn("33 шт. → 33 шт.", "".join(panel.itertext()))
        card = ElementTree.fromstring(decision_brief(self.report)).find("article")
        self.assertEqual(card.find("strong").text, "33 шт. по правилам · 39 шт. в приёмке")
        self.assertIsNotNone(card.find('.//a[@href="#execution-D-01"]'))
        self.assertIn(self.finding["question"], "".join(card.itertext()))

    def test_jira_and_markdown_keep_all_sources_the_question_and_scope(self):
        csv_text = issues_csv(self.report).decode("utf-8-sig")
        body = next(csv.DictReader(StringIO(csv_text)))["Description"]
        md = finding_markdown(self.finding, self.run, {}, self.item)
        for text in (body, md):
            for caption in ("Расчёт по правилам ТЗ · 33 шт.", "Ожидание из приёмки · 39 шт.",
                            "40 шт. − 7 шт. = 33 шт.", "33 шт. → 33 шт.", self.finding["question"],
                            self.item["reason"], "Расчёт двух трактовок не подтверждён"):
                self.assertIn(caption, text)
            self.assertNotIn("Правило Б даёт", text)
            for ref in self.refs:
                self.assertIn(f"Строка {ref['line']} ·", text)
