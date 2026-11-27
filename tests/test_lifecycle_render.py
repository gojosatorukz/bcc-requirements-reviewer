import json
from collections import defaultdict
from pathlib import Path
import unittest

from reviewer.full_render import render_lifecycle
from reviewer.render import render_report


ROOT = Path(__file__).resolve().parents[1]


class LifecycleViewTests(unittest.TestCase):
    def test_partial_expectation_and_open_question_are_separately_labelled_and_source_linked(self):
        checks = [
            {"axis": "initial_action", "status": "needs_decision", "situation": "Первая запись.",
             "action": 'Нажать «Сохранить» при <пустом> поле.', "observation": "Порог ещё не задан.",
             "question": "Что увидит владелец?", "evidence": [{"line": 2, "quote": "<пустое поле>"}]},
            {"axis": "isolation", "status": "specified", "situation": "Две карты.", "action": "Сменить первую.",
             "observation": "У второй прежний лимит.", "question": "", "evidence": [{"line": 5, "quote": "порог карты"}]},
        ]
        backlinks = defaultdict(list)
        cards = render_lifecycle(checks, backlinks)
        self.assertIn('href="#source-line-2"', cards)
        self.assertIn('href="#source-line-5"', cards)
        self.assertIn("Что следует из ТЗ", cards)
        self.assertIn("Вопрос владельцу.</b> Что увидит владелец?", cards)
        self.assertIn("Ожидаемый результат.</b> У второй прежний лимит.", cards)
        self.assertEqual(cards.count("Вопрос владельцу."), 1)
        self.assertIn("&lt;пустое поле&gt;", cards)
        self.assertNotIn("<пустом>", cards)
        self.assertEqual(backlinks[2], [("qa-scenario-1", "QA · Первое действие")])
        self.assertEqual(backlinks[5], [("qa-scenario-2", "QA · Независимость объектов")])

    def test_skipped_axis_explains_applicability_without_reporting_a_test_or_an_expected_outcome(self):
        section = render_lifecycle([], {}, [{"axis": "retry_effect", "reason": "Каталог не описывает изменение."}])
        self.assertIn('id="qa-skipped"', section)
        self.assertIn("Повтор действия.</b> Каталог не описывает изменение.", section)
        self.assertNotIn('id="qa-scenario-1"', section)
        self.assertNotIn("Ожидаемый результат", section)

    def test_historical_full_review_has_no_new_check_or_shortcut(self):
        report = json.loads((ROOT / "runs/bcc-executable/review.json").read_bytes())
        self.assertNotIn("lifecycle_checks", report)
        html = render_report(report)
        self.assertNotIn('id="qa-scenarios"', html)
        self.assertNotIn('href="#qa-scenario-', html)
        self.assertIn('id="execution-D-01"', html)

    def test_full_report_anchors_on_first_action_and_revalidates_the_quote(self):
        report = json.loads((ROOT / "runs/bcc-executable/review.json").read_bytes())
        ref = next(row for row in report["source"] if row["line"] == 37)
        check = {"axis": "initial_action", "status": "needs_decision", "situation": "Настройка отсутствует.",
                 "action": "Открыть экран карты.", "observation": "Уведомление выключено.",
                 "question": "Какой порог увидит владелец?", "evidence": [{"line": 37, "quote": ref["text"]}]}
        report["lifecycle_checks"] = {"version": 1, "checks": [check],
                                      "skipped": [{"axis": axis, "reason": "Не рассматривается в данном тесте."}
                                                  for axis in ("retry_effect", "reentry", "boundary", "isolation")]}
        html = render_report(report)
        self.assertIn('href="#qa-scenario-1">Первое действие</a>', html)
        self.assertIn('href="#qa-scenario-1">↑ QA · Первое действие</a>', html)
        self.assertIn('href="#source-line-37"', html.split('id="qa-scenario-1"', 1)[1].split("</article>", 1)[0])
        self.assertIn("включая 2 критических", html)
        check["evidence"][0]["quote"] = "Такой строки нет"
        with self.assertRaisesRegex(ValueError, "Цитата"):
            render_report(report)
